"""Custom domains of an instance (instances/custom_domains.py), as the core serves them.

    GET    /api/v1/instances/<name>/domains                    the instance's domains and how to set them up
    DELETE /api/v1/instances/<name>/domains/<domain>           remove it and its certificate -> 204

Adding a domain, making one primary and checking one again come with the premium "domains" package (premium/),
which installs automatically from Sudoo.sh when the plan includes it; without it those calls answer 402 with the
plans that have it. Removing a domain is always possible, and domains set up before keep being served.

Reading needs instances.view; changes need domains.manage (administrators) and are audit-logged. On a production
instance every change also needs "confirm_production": true: a domain change moves where its users reach it.

The answer of GET (also the live event "domains", key: the instance) is one object:

    {"mode": "off" | "acme" | "external", "limit", "default_url", "target": {"host", "record_type"} | null,
     "exposure", "listener_error", "authority": "production" | "staging" | "custom",
     "domains": [{"domain", "primary", "status", "reason", "tls", "dns_records": [{"type", "name", "value"}],
                  "addresses", "added_at", "added_by", "checked_at", "next_check_at", "renews_at",
                  "certificate": {"issued_at", "expires_at", "issuer", "staging"} | null, "url",
                  "moved_from", "dns_pending"}]}

status: waiting_dns, issuing, active, renewing, expiring, failed (domains/__init__.py). moved_from: the server the
domain moved here from with its instance (several servers), or null; dns_pending: its DNS does not point at this
server yet (the reason says where to point it; dns_records are this server's).
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort

from sudoo import exposure, premium
from sudoo.config import settings
from sudoo.instances import custom_domains as domains
from sudoo.instances import gateway, stages, store

from .. import validation as check
from ..security import log, require
from .instances import existing
from .responses import iso, no_content, ok

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
        "dns_records": _dns().instructions(record["domain"]) if record.get("tls") == "acme" and _dns() else [],
        "addresses": list(record.get("addresses") or []),
        "added_at": iso(record.get("added_at")),
        "added_by": record.get("added_by"),
        "checked_at": iso(record.get("checked_at")),
        "next_check_at": (
            iso(record.get("next_attempt_at"))
            if status in ("waiting_dns", "failed", "renewing") or record.get("dns_pending")
            else None
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
        "moved_from": (record.get("moved") or {}).get("from") or None,
        "dns_pending": bool(record.get("dns_pending")),
    }


def _dns() -> Any:
    """The domains package's DNS helper (instructions, target), or None without it."""
    manager = premium.hook(premium.DOMAINS, "manager") if premium.loaded("domains") else None
    return getattr(manager, "dns", None)


def domains_resource(name: str) -> dict[str, Any]:
    """The instance's domains and how to set them up (also the live event "domains")."""
    goal = _dns().target() if _dns() else None
    errors = [gateway.LISTENER_ERRORS.get(key) for key in (gateway.HTTP_LISTENER, gateway.HTTPS_LISTENER)]
    directory = settings.acme_directory
    return {
        "mode": str(settings.custom_domains),
        "managed": premium.loaded("domains"),
        "premium": None if premium.loaded("domains") else premium.refusal("domains"),
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
