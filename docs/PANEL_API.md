# Panel API

**Audience:** developers connecting other systems to the panel: CI pipelines, chat bots, scripts, dashboards.
**Covers:** API tokens, how to sign a request, every endpoint, and a ready-to-use client.

This is the API of the **panel** (create, change, restart, delete instances). To talk to the **Odoo** inside an
instance, see [API and webhooks](API.md).

---

## Contents

1. [Getting a token](#1-getting-a-token)
2. [Signing a request](#2-signing-a-request)
3. [Endpoints](#3-endpoints)
4. [Examples](#4-examples)
5. [Errors](#5-errors)
6. [Security notes](#6-security-notes)

---

## 1. Getting a token

An administrator creates tokens in the panel: **API tokens** in the top bar, then **Create token**, with a name
saying what will use it and a scope:

| Scope | Allows |
| :--- | :--- |
| `read` | List and read instances, jobs and versions |
| `write` | Everything `read` allows, plus create, change, run actions on and delete instances |

The panel shows two values **once**: the **token** (`oimt_...`) and the **signing secret**. Store both where the other
system keeps secrets. A token can be revoked at any time on the same page; it stops working at once.

On the server, a token can also be created from the command line:

```bash
docker exec oim-panel instance-manager create-token "CI pipeline" write
```

---

## 2. Signing a request

Every request carries four headers:

| Header | Value |
| :--- | :--- |
| `Authorization` | `Bearer <token>` |
| `X-OIM-Timestamp` | the current time, Unix seconds, for example `1794430123` |
| `X-OIM-Nonce` | 16 to 64 random letters, digits, `-` or `_`; new for every request |
| `X-OIM-Signature` | `sha256=` and the hex HMAC-SHA256 of the text below, with the signing secret as the key |

The signed text is five lines joined with `\n` (no newline at the end):

```
<X-OIM-Timestamp>
<X-OIM-Nonce>
<METHOD in capitals>
<path, with ?query if there is one>
<hex SHA-256 of the exact request body; of an empty body when there is none>
```

For example, `GET /api/v1/instances` at `1794430123` with nonce `p4Xq9mW2...`:

```
1794430123
p4Xq9mW2...
GET
/api/v1/instances
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

Requests with a body send it as JSON (`Content-Type: application/json`) and sign the exact bytes sent.

What each part protects against:

| Part | Stops |
| :--- | :--- |
| Token | Unknown callers. Revoking it ends access at once. |
| Signature over method, path and body | A request being changed on the way, and anyone who only has the token (from a log, a proxy, a screenshot) |
| Timestamp (within 5 minutes) | Old requests being sent again later |
| Nonce (each one accepted once) | The same request being sent twice within those 5 minutes |

---

## 3. Endpoints

All paths start with `/api/v1`. Answers are JSON. Actions run in the background and answer **202** with a job id
right away; follow the job with `GET /api/v1/jobs/<id>`.

| Method and path | Scope | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /instances` | read | | `{"instances": [{name, status, url, version, edition, branch, ...}]}` |
| `GET /instances/<name>` | read | | details: database, automation settings, lifetime, commits, submodules, recent jobs (never passwords) |
| `GET /versions` | read | | `{"versions": [{"version": "19.0", "enterprise": true}, ...]}` |
| `GET /jobs/<id>` | read | | `{status, steps, notes, log, hint}`; `status` is `queued`, `running`, `success` or `failed` |
| `POST /instances` | write | as below | **202** `{"job", "name"}` |
| `PATCH /instances/<name>` | write | any of `auto_update`, `auto_upgrade`, `auto_pause` (true or false), `delete_after_days` (0 keeps it) | the new details |
| `POST /instances/<name>/actions/<action>` | write | `{}`, or the action's fields | **202** `{"job"}` |
| `DELETE /instances/<name>` | write | `{"confirm": "<name>"}` | **202** `{"job"}`; a backup is taken first |

Creating an instance takes the same fields as the **New instance** form:

```json
{
  "repo": "https://github.com/acme-co/sales",
  "branch": "main",
  "version": "19.0",
  "edition": "community",
  "name": "",
  "install_modules": true,
  "demo": false,
  "delete_after_days": 30
}
```

Actions:

| Action | Body |
| :--- | :--- |
| `update`, `update-submodules`, `restart`, `start`, `pause`, `switch-off`, `backup` | `{}` |
| `upgrade` | `{"modules": "sale_custom,stock_custom"}` or `{"modules": "all"}` |
| `switch-branch` | `{"branch": "main"}` |
| `add-submodule` | `{"url": "https://github.com/OCA/web", "path": "oca/web", "branch": "19.0"}` |
| `remove-submodule` | `{"path": "oca/web"}` |

---

## 4. Examples

**Python**: [`examples/panel_api_client.py`](../examples/panel_api_client.py) is a complete client using only the
standard library. Copy it into your project:

```python
from panel_api_client import PanelAPI

api = PanelAPI("https://panel.example.com", token="oimt_...", signing_secret="...")
for instance in api.instances():
    print(instance["name"], instance["status"], instance["url"])

job = api.action("sales-main", "update")           # waits until the update is finished
print(job["status"])                                # success or failed

created = api.request("POST", "/api/v1/instances", {
    "repo": "https://github.com/acme-co/sales", "branch": "feature-x", "version": "19.0",
    "edition": "community", "install_modules": True, "delete_after_days": 7,
})
print(api.wait(created["job"])["status"], created["name"])
```

**Shell** (bash with openssl):

```bash
URL=https://panel.example.com TOKEN=oimt_... SECRET=...
call() {  # call METHOD PATH [JSON]
  local body="${3:-}" ts nonce sig
  ts=$(date +%s); nonce=$(openssl rand -hex 16)
  sig=$(printf '%s\n%s\n%s\n%s\n%s' "$ts" "$nonce" "$1" "$2" "$(printf '%s' "$body" | sha256sum | cut -d' ' -f1)" \
        | openssl dgst -sha256 -hmac "$SECRET" -hex | awk '{print $NF}')
  curl -s -X "$1" "$URL$2" -H "Authorization: Bearer $TOKEN" -H "X-OIM-Timestamp: $ts" \
       -H "X-OIM-Nonce: $nonce" -H "X-OIM-Signature: sha256=$sig" \
       ${body:+-H "Content-Type: application/json" --data "$body"}
}
call GET /api/v1/instances
call POST /api/v1/instances/sales-main/actions/restart '{}'
```

**A CI pipeline** that gives every pull request its own instance: create an instance from the pull request's branch
with `delete_after_days: 7`, post its `url` as a comment, and delete it when the pull request is closed.

---

## 5. Errors

Every error answers `{"error": "<what is wrong and what to do>"}`.

| Status | Meaning |
| :--- | :--- |
| 400 | A field is missing or not valid; the message says which and what to send |
| 401 | Token, timestamp, nonce or signature not accepted; the message says which |
| 403 | The token's scope does not allow this (a `read` token sending a change) |
| 404 | No such instance, job or action |
| 415 | The body is not sent as JSON |
| 429 | Too many refused calls from this address: 5 in 15 minutes block it for 15 minutes |

---

## 6. Security notes

* The token is stored only as a SHA-256 hash, and the signing secret encrypted with the vault key. Neither is ever
  shown again; a lost one means creating a new token.
* A `write` token can do everything an administrator can do with instances, including deleting them. Give each system
  its own token, with the `read` scope where reading is enough, and revoke tokens that are no longer used.
* Every call is in the audit log as `api:<token name>`, and every refused call with its reason and address.
* The API never returns passwords (Odoo admin, master password) or the server's ports.
* The panel's browser session does not open the API, and API tokens do not open the panel's pages.
