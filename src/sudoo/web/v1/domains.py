"""Custom domains of an instance (domains/).

    GET    /api/v1/instances/<name>/domains                    the instance's domains and how to set them up
    POST   /api/v1/instances/<name>/domains                    add {"domain"} -> 201, Location: the domain
    PATCH  /api/v1/instances/<name>/domains/<domain>           {"primary": true}: make it the primary domain
    POST   /api/v1/instances/<name>/domains/<domain>/check     check DNS (and issue) now instead of later
    DELETE /api/v1/instances/<name>/domains/<domain>           remove it and its certificate -> 204

Reading needs instances.view; changes need domains.manage (administrators) and are audit-logged. On a production
instance every change also needs "confirm_production": true: a domain change moves where its users reach it.

The answer of GET (also the live event "domains", key: the instance) is one object:

    {"mode": "off" | "acme" | "external", "limit", "default_url", "target": {"host", "record_type"} | null,
     "exposure", "listener_error", "authority": "production" | "staging" | "custom",
     "domains": [{"domain", "primary", "status", "reason", "tls", "dns_records": [{"type", "name", "value"}],
                  "addresses", "added_at", "added_by", "checked_at", "next_check_at", "renews_at",
                  "certificate": {"issued_at", "expires_at", "issuer", "staging"} | null, "url"}]}

status: waiting_dns, issuing, active, renewing, expiring, failed (domains/__init__.py).
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort, g

from sudoo import domains, exposure
from sudoo.config import settings
from sudoo.domains import dns
from sudoo.instances import gateway, stages, store

from .. import validation as check
from ..security import log, refuse_without_feature, require
from .instances import existing
from .responses import PREFIX, iso, no_content, ok

blueprint = Blueprint("domains", __name__)


def domain_resource(record: dict[str, Any]) -> dict[str, Any]:
    certificate = record.get("certificate")
    status = domains.shown_status(record)
    return {
        "domain": record["domain"],
        "primary": bool(record.get("primary")),
        "status": status,
        "reason": record.get("reason"),
        "tls": record.get("tls"),
        "dns_records": dns.instructions(record["domain"]) if record.get("tls") == "acme" else [],
        "addresses": list(record.get("addresses") or []),
        "added_at": iso(record.get("added_at")),
        "added_by": record.get("added_by"),
        "checked_at": iso(record.get("checked_at")),
        "next_check_at": (
            iso(record.get("next_attempt_at")) if status in ("waiting_dns", "failed", "renewing") else None
        ),
        "renews_at": iso(record.get("renew_at")) if certificate else None,
        "certificate": (
            {
                "issued_at": iso(certificate.get("issued_at")),
                "expires_at": iso(certificate.get("expires_at")),
                "issuer": certificate.get("issuer"),
                "staging": bool(certificate.get("staging")),
            }
            if certificate
            else None
        ),
        "url": f"https://{record['domain']}" if domains.serving(record) else None,
    }


def domains_resource(name: str) -> dict[str, Any]:
    """The instance's domains and how to set them up (also the live event "domains")."""
    goal = dns.target()
    errors = [gateway.LISTENER_ERRORS.get(key) for key in (gateway.HTTP_LISTENER, gateway.HTTPS_LISTENER)]
    directory = settings.acme_directory
    return {
        "mode": domains.mode(),
        "limit": settings.domains_per_instance,
        "default_url": exposure.default_address(name) if store.exists(name) else None,
        "target": {"host": goal.host, "record_type": goal.record_type()} if goal else None,
        "exposure": settings.exposure,
        "listener_error": next((e for e in errors if e), None),
        "authority": directory if directory in ("production", "staging") else "custom",
        "domains": [
            domain_resource(record) for record in domains.for_instance(name) if record.get("tls") == domains.tls_mode()
        ],
    }


def _confirm_production(name: str, what: str) -> None:
    if (
        stages.of(store.read_env(name)) == stages.PRODUCTION
        and check.flag(check.body(), "confirm_production") is not True
    ):
        abort(
            400,
            f"{name} is a production instance: confirm that you want to {what} "
            '("confirm_production": true). Its users reach it through its domains.',
        )


def _own(name: str, domain: str) -> dict[str, Any]:
    record = domains.get(domain.lower())
    if record is None or record.get("instance") != name:
        abort(404, "This instance has no such domain.")
    return record


def _refused(error: Exception) -> Response:
    status = error.status if isinstance(error, domains.Refused) else 400
    abort(status, str(error))


@blueprint.get("/instances/<name>/domains")
@require("instances.view")
def read(name: str) -> Response:
    return ok(domains_resource(existing(name)))


@blueprint.post("/instances/<name>/domains")
@require("domains.manage")
def add(name: str) -> Response:
    existing(name)
    refuse_without_feature("custom_domains", f"add a custom domain to {name}")  # those added before keep serving
    value = check.text(check.body(), "domain", max_length=300)
    if not value:
        abort(400, "Enter the domain, such as erp.example.com.")
    _confirm_production(name, "add a domain to it")
    try:
        record = domains.add(name, value, g.user["name"])
    except (domains.InvalidDomain, domains.Refused) as error:
        log("custom domain refused", instance=name, detail=f"{value[:100]}: {error}", ok=False)
        return _refused(error)
    log("custom domain added", instance=name, detail=record["domain"])
    response = ok(domain_resource(record), status=201)
    response.headers["Location"] = f"{PREFIX}/instances/{name}/domains/{record['domain']}"
    return response


@blueprint.patch("/instances/<name>/domains/<domain>")
@require("domains.manage")
def change(name: str, domain: str) -> Response:
    existing(name)
    record = _own(name, domain)
    if check.flag(check.body(), "primary") is not True:
        abort(400, 'Send {"primary": true} to make this the primary domain.')
    if not record.get("primary"):
        _confirm_production(name, "change its primary domain")
        try:
            record = domains.make_primary(record["domain"])
        except domains.Refused as error:
            return _refused(error)
        log("primary custom domain changed", instance=name, detail=record["domain"])
    return ok(domain_resource(record))


@blueprint.post("/instances/<name>/domains/<domain>/check")
@require("domains.manage")
def check_now(name: str, domain: str) -> Response:
    existing(name)
    record = _own(name, domain)
    try:
        record = domains.retry(record["domain"])
    except domains.Refused as error:
        return _refused(error)
    log("custom domain checked again", instance=name, detail=record["domain"])
    return ok(domain_resource(record))


@blueprint.delete("/instances/<name>/domains/<domain>")
@require("domains.manage")
def remove(name: str, domain: str) -> Response:
    existing(name)
    record = _own(name, domain)
    _confirm_production(name, f"remove {record['domain']}")
    try:
        domains.remove(record["domain"])
    except domains.Refused as error:
        return _refused(error)
    log("custom domain removed", instance=name, detail=record["domain"])
    return no_content()
