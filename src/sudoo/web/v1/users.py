"""Accounts and the audit log.

    GET    /api/v1/roles                         the roles, what each may do, and every permission
    GET    /api/v1/users                         accounts
    POST   /api/v1/users                         create: {"email", "role", "first_name", "last_name", "phone",
                                                 "name" (optional: made from the e-mail address), "password"
                                                 (optional: generated), "must_change_password" (default true)};
                                                 the password is in this answer only -> 201
    PATCH  /api/v1/users/<name>                  change the role or the profile (also your own), how the account
                                                 signs in: {"sign_in_methods": ["password", "email_code", "github"]}
                                                 (null: the server's default), {"github_login"} (linked at the first
                                                 sign-in with GitHub)
    POST   /api/v1/users/<name>/password         set a new password: {"password"} (optional: generated),
                                                 {"must_change_password"} (default true); returns it once
    DELETE /api/v1/users/<name>/second-factors   remove all second factors and recovery codes (a lost phone) -> 204
    DELETE /api/v1/users/<name>/github           unlink the account's GitHub account -> 204
    DELETE /api/v1/users/<name>                  delete -> 204
    GET    /api/v1/audit                         audit-log entries, newest first unless sorted: limit (at most
                                                 2000), offset, sort (ts, user, event, instance, ip, ok; "-" first
                                                 for descending), filters user, event, instance, ip, ok
                                                 (allowed|refused), since, until (ISO 8601 or Unix time), project,
                                                 branch, q (text in any field); meta.total, meta.limit, meta.offset
    GET    /api/v1/audit/facets                  the users, events and projects there are, for the filters
    GET    /api/v1/audit/export?format=csv|xlsx  the entries the same filters choose as a file: a year at most
                                                 (since defaults to a year before until), AUDIT_EXPORT_MAX_ROWS rows
                                                 at most (meta in X-Total-Count, X-Exported-Count); audit-logged

Only a super admin creates, changes or deletes super admins; there is always at least one, and at least one of
them keeps signing in with a password (auth/methods.py, break glass). A client account stays a client (and staff
never become clients); while a client has closed one of its projects or branches, its account is not changed, reset
or deleted by anyone (projects/clients.py). Accounts created or reset
here choose their own password at their next sign-in, unless must_change_password is false. New people are normally
invited by e-mail instead (invitations.py); POST /users is refused unless INVITATION_PASSWORD_FALLBACK allows it
("no_mail", the default: only while e-mail is not set up).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from flask import Blueprint, Response, abort, g, request

from sudoo import accounts, invitations, roles, teams
from sudoo.auth import github, github_access, methods
from sudoo.config import settings
from sudoo.core import audit
from sudoo.mail import mailer
from sudoo.projects import access

from .. import validation as check
from ..security import log, require
from .account import factors_of, github_resource
from .responses import PREFIX, created, iso, no_content, ok

blueprint = Blueprint("users", __name__)


def user_resource(name: str, user: dict[str, Any]) -> dict[str, Any]:
    role = roles.normalize(str(user.get("role")))
    return {
        "name": name,
        "display_name": accounts.display_name(name, user),
        **{key: user.get(key) for key in accounts.PROFILE_FIELDS},
        "role": role,
        "role_label": accounts.ROLES[role],
        "last_sign_in_at": iso(user.get("last_login")),
        "password_changed_at": iso(user.get("password_changed")),
        "must_change_password": bool(user.get("must_change_password")),
        "second_factors": [f["name"] for f in factors_of(user) if f["enrolled"]],
        "sign_in_methods": methods.of(user),
        "sign_in_methods_chosen": methods.own_choice(user) is not None,
        "github": github_resource(user),
        "github_login": user.get(github.LOGIN_FIELD),
        "has_password": bool(user.get("hash")),
        "links": {"self": f"{PREFIX}/users/{name}"},
    }


def _guard(role: str) -> None:
    """Only a super admin creates, changes or deletes super admins."""
    if role == "super_admin" and g.user["role"] != "super_admin":
        abort(403, "Only a super admin can manage super admin accounts.")


def github_app_login(login: str) -> bool:
    import re

    return re.fullmatch(r"[A-Za-z0-9](?:-?[A-Za-z0-9]){0,38}", login) is not None


def _refuse_without_break_glass(name: str, values: dict[str, Any] | None) -> None:
    """Refuse a change (None: the deletion) that would leave no super admin signing in with a password."""
    before = accounts.all_users()
    after = dict(before)
    if values is None:
        after.pop(name, None)
    else:
        merged = {**before[name], **values}
        after[name] = {k: v for k, v in merged.items() if v is not None}
    problem = methods.break_glass_problem(before, after)
    if problem:
        abort(400, problem)


def _client_guard(name: str) -> None:
    """409 while `name` is a client of a project or branch it closed: staff would take its place
    (projects/clients.py); audit-logged."""
    from sudoo.projects import clients

    refused = clients.account_refusal(name)
    if refused:
        log("refused: change a client's account (its project is closed)", ok=False, detail=name)
        abort(409, refused)


def _account(name: str) -> dict[str, Any]:
    check.username(name)
    return accounts.get(name) or abort(404, "No such account.")


def _profile(data: dict[str, Any]) -> dict[str, Any]:
    return {key: check.text(data, key, max_length=120) for key in accounts.PROFILE_FIELDS if key in data}


@blueprint.get("/roles")
def list_roles() -> Response:
    return ok(
        {
            "roles": [
                {
                    "name": name,
                    "label": role.label,
                    "description": role.description,
                    "permissions": sorted(roles.permissions(name)),
                }
                for name, role in roles.ROLES.items()
            ],
            "permissions": roles.PERMISSIONS,
        }
    )


@blueprint.get("/users")
@require("accounts.manage")
def list_users() -> Response:
    return ok([user_resource(name, user) for name, user in sorted(accounts.all_users().items())])


def _password(data: dict[str, Any], name: str, profile: dict[str, Any]) -> tuple[str | None, bool]:
    """The typed password (checked against the rules) or None for a generated one, and whether it must change."""
    must_change = data.get("must_change_password", True)
    if not isinstance(must_change, bool):
        abort(400, "must_change_password must be true or false.")
    if not data.get("password"):
        return None, True
    password = check.secret(data, "password")
    names = (str(profile.get("first_name") or ""), str(profile.get("last_name") or ""))
    problem = accounts.password_problem(password, name, profile.get("email"), names)
    if problem:
        abort(400, problem)
    return password, must_change


@blueprint.post("/users")
@require("accounts.manage")
def create_user() -> Response:
    data = check.body()
    role = check.one_of(check.text(data, "role"), roles.ROLES, "Choose a role from the list.")
    _guard(role)
    if not invitations.password_allowed(mailer().ready):
        abort(
            400,
            "Invite the person instead: they receive an e-mail and choose their own password "
            "(INVITATION_PASSWORD_FALLBACK).",
        )
    profile = _profile(data)
    if not profile.get("email"):
        abort(400, "Enter the person's e-mail address: they sign in with it.")
    problem = accounts.profile_problem(profile)
    if problem:
        abort(400, problem)
    chosen = check.text(data, "name", max_length=32).lower()
    name = check.username(chosen) if chosen else accounts.username_for(profile["email"])
    typed, must_change = _password(data, name, profile)
    try:
        password = accounts.create(name, role, typed, must_change=must_change, **profile)
    except ValueError as error:
        abort(400, str(error))
    log("account created", detail=f"{name} ({accounts.ROLES[role]})")
    return created(user_resource(name, accounts.get(name) or {}) | {"password": password}, f"{PREFIX}/users/{name}")


@blueprint.patch("/users/<name>")
@require("accounts.manage")
def change_user(name: str) -> Response:
    account = _account(name)
    _guard(roles.normalize(account["role"]))
    data = check.body()
    values = _profile(data)
    problem = accounts.profile_problem(values)
    if problem:
        abort(400, problem)
    if "email" in values and accounts.email_taken(values["email"], other_than=name):
        abort(400, "Another account already uses this e-mail address.")
    if name != g.user["name"]:
        _client_guard(name)
    if "role" in data:
        role = check.one_of(check.text(data, "role"), roles.ROLES, "Choose a role from the list.")
        _guard(role)
        if role != account["role"] and roles.CLIENT in (role, account["role"]):
            abort(409, "A client account stays a client, and staff never become clients: create another account.")
        if account["role"] == "super_admin" and role != "super_admin":
            others = [n for n, u in accounts.all_users().items() if u["role"] == "super_admin" and n != name]
            if not others:
                abort(400, "The last super admin must stay a super admin.")
        values["role"] = role
    if "sign_in_methods" in data:
        try:
            values[methods.FIELD] = (
                None if data["sign_in_methods"] is None else methods.checked(data["sign_in_methods"])
            )
        except ValueError as error:
            abort(400, str(error))
    if "github_login" in data:
        login = check.text(data, "github_login", max_length=39).strip().lstrip("@")
        if login and not github_app_login(login):
            abort(400, "github_login: a GitHub user name (letters, digits and single dashes, at most 39).")
        values[github.LOGIN_FIELD] = login
    _refuse_without_break_glass(name, values)
    from sudoo import projects
    from sudoo.projects import notices

    watched = [p["id"] for p in projects.all_projects()] if "role" in values else []
    with notices.watch(watched, by=accounts.display_name(g.user["name"], accounts.get(g.user["name"]) or {})):
        accounts.update(name, **{key: (value or None) for key, value in values.items()})
    if "role" in values:
        accounts.end_sessions(name)  # the new permissions apply from the next sign-in
    log("account changed", detail=f"{name}: {', '.join(sorted(values))}")
    return ok(user_resource(name, accounts.get(name) or {}))


@blueprint.post("/users/<name>/password")
@require("accounts.manage")
def reset_password(name: str) -> Response:
    account = _account(name)
    if name == g.user["name"]:
        abort(400, "Use Change password for your own account.")
    _guard(roles.normalize(account["role"]))
    _client_guard(name)
    data = check.body()
    typed, must_change = _password(data, name, account)
    password = typed or accounts.generate_password()
    accounts.set_password(name, password, must_change=must_change)
    log("password reset", detail=name)
    return ok({"password": password})


@blueprint.delete("/users/<name>/second-factors")
@require("accounts.manage")
def reset_second_factors(name: str) -> Response:
    _guard(roles.normalize(_account(name)["role"]))
    _client_guard(name)
    accounts.update(name, second_factors=None, totp_pending=None, recovery_codes=None)  # the codes go with the app
    accounts.end_sessions(name)
    log("second factors removed", detail=name)
    return no_content()


@blueprint.delete("/users/<name>/github")
@require("accounts.manage")
def unlink_github(name: str) -> Response:
    account = _account(name)
    _guard(roles.normalize(account["role"]))
    link = github.link_of(account)
    if link is None:
        abort(404, "No GitHub account is linked to this account.")
    github.unlink(name)
    github_access.forget(name)
    accounts.end_sessions(name)
    log("GitHub account unlinked", detail=f"{name}: {link.get('login')} (GitHub id {link['id']})")
    return no_content()


@blueprint.delete("/users/<name>")
@require("accounts.manage")
def delete_user(name: str) -> Response:
    account = _account(name)
    if name == g.user["name"]:
        abort(400, "You cannot delete the account you are signed in with.")
    _guard(roles.normalize(account["role"]))
    _client_guard(name)
    _refuse_without_break_glass(name, None)
    try:
        accounts.delete(name)
    except ValueError as error:
        abort(400, str(error))
    github_access.forget(name)
    in_teams = teams.forget_member(name)  # out of every team, grant and lock (projects/access.py)
    in_projects = access.forget_user(name)
    access.forget_restrictions()
    log("account deleted", detail=name + (f"; removed from teams {', '.join(in_teams)}" if in_teams else "")
        + (f"; removed from the access of {', '.join(in_projects)}" if in_projects else ""))  # fmt: skip
    return no_content()


def _when(name: str) -> int | None:
    """A time filter: ISO 8601 (a date alone is midnight UTC; no zone is UTC) or a Unix time; None when absent."""
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        abort(400, f"{name} must be a date and time (ISO 8601) or a Unix time.")
    return int((parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp())


def _audit_filters() -> dict[str, Any]:
    filters: dict[str, Any] = {}
    for key in ("user", "event", "instance", "ip", "project", "branch"):
        value = (request.args.get(key) or "").strip()
        if len(value) > 200:
            abort(400, f"{key} is too long.")
        if value:
            filters[key] = value
    if filters.get("branch") and not filters.get("project"):
        abort(400, "Choose the project of the branch too.")
    text = (request.args.get("q") or "").strip()
    if len(text) > 200:
        abort(400, "The search is too long (at most 200 characters).")
    if text:
        filters["text"] = text
    shown = request.args.get("ok") or ""
    if shown:
        filters["ok"] = check.one_of(shown, ("allowed", "refused"), 'ok is "allowed" or "refused".') == "allowed"
    since, until = _when("since"), _when("until")
    if since is not None and until is not None and since >= until:
        abort(400, "since must be before until.")
    filters.update({k: v for k, v in (("since", since), ("until", until)) if v is not None})
    return filters


def _sort() -> tuple[str, bool]:
    raw = request.args.get("sort") or "-ts"
    descending = raw.startswith("-")
    column = raw.lstrip("-")
    if column not in audit.SORTS:
        abort(400, f"sort is one of {', '.join(audit.SORTS)}, with \"-\" first for descending.")
    return column, descending


def _with_support(entry: dict[str, Any]) -> dict[str, Any]:
    from sudoo.premium import stubs

    return {**entry, "support": stubs.support_entry(entry.get("user"))}


@blueprint.get("/audit")
@require("audit.view")
def audit_log() -> Response:
    limit = check.whole_number(request.args.get("limit", "200"), "limit", minimum=1, maximum=2000)
    offset = check.whole_number(request.args.get("offset", "0"), "offset", minimum=0, maximum=10**7)
    column, descending = _sort()
    found, total = audit.search(_audit_filters(), column, descending, limit, offset)
    return ok([_with_support(entry) for entry in found], meta={"total": total, "limit": limit, "offset": offset})


@blueprint.get("/audit/facets")
@require("audit.view")
def audit_facets() -> Response:
    from sudoo import projects

    found = audit.facets()
    listed = [{"id": p["id"], "name": p.get("name") or p["id"]} for p in projects.all_projects()]
    return ok({**found, "projects": listed, "export_max_rows": int(settings.audit_export_max_rows)})


@blueprint.get("/audit/export")
@require("audit.view")
def audit_export() -> Response:
    chosen = check.one_of(request.args.get("format", "csv"), ("csv", "xlsx"), 'format is "csv" or "xlsx".')
    filters = _audit_filters()
    until = filters.get("until") or int(time.time()) + 1
    earliest = until - audit.EXPORT_MAX_DAYS * 86400
    if filters.get("since", earliest) < earliest:
        abort(400, f"An export covers {audit.EXPORT_MAX_DAYS} days at most: choose a later start.")
    filters["since"] = max(filters.get("since", earliest), earliest)
    column, descending = _sort()
    limit = int(settings.audit_export_max_rows)
    rows, total = audit.export_rows(filters, column, descending, limit)
    shown = {k: v for k, v in filters.items() if k not in ("since", "until")}
    log("audit log exported", detail=f"{chosen}, {len(rows)} of {total} entries, from {iso(filters['since'])}"
        f"{' to ' + str(iso(filters['until'])) if filters.get('until') else ''}"
        f"{', ' + ', '.join(f'{k}={v}' for k, v in shown.items()) if shown else ''}"[:300])  # fmt: skip
    body = audit.to_xlsx(rows) if chosen == "xlsx" else audit.to_csv(rows)
    mimetype = XLSX_TYPE if chosen == "xlsx" else "text/csv; charset=utf-8"
    response = Response(body, mimetype=mimetype)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    response.headers["Content-Disposition"] = f'attachment; filename="audit-{stamp}.{chosen}"'
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Exported-Count"] = str(len(rows))
    return response


XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
