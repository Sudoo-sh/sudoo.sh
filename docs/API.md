# API and Webhooks

**Audience:** developers connecting other systems (scripts, Postman, n8n, a website, another Odoo) to an instance.
**Covers:** Odoo's APIs through an instance's link, webhooks in both directions, files, and the limits to plan for.

---

## Contents

1. [The basics](#1-the-basics)
2. [XML-RPC](#2-xml-rpc)
3. [JSON-RPC](#3-json-rpc)
4. [JSON-2 API with an API key (Odoo 19 and later)](#4-json-2-api-with-an-api-key-odoo-19-and-later)
5. [Webhooks](#5-webhooks)
6. [Files](#6-files)
7. [Limits and good habits](#7-limits-and-good-habits)
8. [The panel's own API](#8-the-panels-own-api)

---

## 1. The basics

An instance's link is a normal Odoo address. Every Odoo API works through it exactly as on any Odoo server: use the
link from the panel as the base address, and the database name shown on the instance's page.

| You need | Where to find it |
| :--- | :--- |
| Base address | The instance's link, for example `https://river-stone-apple.trycloudflare.com` |
| Database | The instance's page, **Database** (for example `sales_main`) |
| User and password | `admin` / `admin`, or a user you created in that Odoo |
| API key (JSON-2) | Created in that Odoo, see [section 4](#4-json-2-api-with-an-api-key-odoo-19-and-later) |

All of the following were tested end to end through a public link on Odoo 20 Enterprise: XML-RPC, JSON-RPC, the
JSON-2 API, an incoming webhook, a 5 MB upload and download, 30 parallel calls, WebSocket, a call to a paused instance,
and two instances signed in at the same time.

---

## 2. XML-RPC

Works with every Odoo version and every language that has an XML-RPC library.

```python
import xmlrpc.client

url, db, user, password = "https://river-stone-apple.trycloudflare.com", "sales_main", "admin", "admin"

common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
uid = common.authenticate(db, user, password, {})

models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
partners = models.execute_kw(db, uid, password, "res.partner", "search_read",
                             [[["is_company", "=", True]]], {"fields": ["name", "email"], "limit": 5})
new_id = models.execute_kw(db, uid, password, "res.partner", "create", [{"name": "API test"}])
models.execute_kw(db, uid, password, "res.partner", "write", [[new_id], {"phone": "+970 59 000 0000"}])
models.execute_kw(db, uid, password, "res.partner", "unlink", [[new_id]])
```

---

## 3. JSON-RPC

What Odoo's own web client uses. Sign in once, keep the session cookie, then call any method.

```python
import requests

url = "https://river-stone-apple.trycloudflare.com"
s = requests.Session()
s.post(f"{url}/web/session/authenticate", json={
    "jsonrpc": "2.0", "method": "call",
    "params": {"db": "sales_main", "login": "admin", "password": "admin"},
}).raise_for_status()

r = s.post(f"{url}/web/dataset/call_kw", json={
    "jsonrpc": "2.0", "method": "call",
    "params": {"model": "res.partner", "method": "search_read", "args": [[]],
               "kwargs": {"fields": ["name"], "limit": 5}},
})
print(r.json()["result"])
```

---

## 4. JSON-2 API with an API key (Odoo 19 and later)

The modern API: one HTTP request per call, authenticated with an API key instead of a password.

**Create an API key** in the instance: avatar (top right), **My Profile** or **Preferences**, **Account Security**,
**New API Key**. Give it a name and, on Odoo 20, an **expiry date** (a key without one is refused with 401).
Copy the key; Odoo shows it only once.

```bash
curl -X POST "https://river-stone-apple.trycloudflare.com/json/2/res.partner/search_read" \
     -H "Authorization: bearer <api key>" \
     -H "Content-Type: application/json" \
     -d '{"domain": [["is_company", "=", true]], "fields": ["name", "email"], "limit": 5}'
```

```python
import requests

url, key = "https://river-stone-apple.trycloudflare.com", "<api key>"
r = requests.post(f"{url}/json/2/res.partner/create",
                  headers={"Authorization": f"bearer {key}"},
                  json={"vals_list": [{"name": "Created with JSON-2"}]})
print(r.json())          # the new record ids
```

If the instance has several databases, add the header `X-Odoo-Database: <database>`. Instances have one, so it is not
needed.

---

## 5. Webhooks

**Into Odoo (another system calls the instance).** In the instance, install **Automation Rules** (`base_automation`), create
a rule with the trigger **On webhook**, and choose what it does (for example create a lead from the received data).
Odoo shows the rule's address, `https://<instance link>/web/hook/<random id>`. Give that address to the other system:

```bash
curl -X POST "https://river-stone-apple.trycloudflare.com/web/hook/3f6c1b2e-..." \
     -H "Content-Type: application/json" \
     -d '{"name": "Lead from the website", "email": "visitor@example.com"}'
```

**Out of Odoo (the instance calls another system).** Create an automation rule with the action **Send webhook
notification** and the other system's address. Calls go out from the server directly, so the other system only
needs to be reachable from the internet.

**After a server restart** the instance's link changes, and so does the address of every incoming webhook. Odoo is
told its new address automatically (the rule shows the new one), but the other system must be updated by hand.

---

## 6. Files

Upload with `ir.attachment`. On Odoo 20 use the field `raw` with the file's content in base64 (the older `datas`
field is refused there):

```python
import base64

with open("contract.pdf", "rb") as fh:
    content = base64.b64encode(fh.read()).decode()
attachment_id = models.execute_kw(db, uid, password, "ir.attachment", "create",
                                  [{"name": "contract.pdf", "raw": content, "res_model": "res.partner", "res_id": 7}])
```

Download with a signed-in session (see [JSON-RPC](#3-json-rpc)):

```python
data = s.get(f"{url}/web/content/{attachment_id}?download=true").content
```

---

## 7. Limits and good habits

| Limit or behaviour | What to do |
| :--- | :--- |
| A paused instance answers `503` with `Retry-After` for 20 to 60 seconds while it starts | Retry after the given number of seconds. Most HTTP libraries and webhook senders do this already. |
| An instance being updated answers `503` for a minute or two | Same as above. |
| The link changes after a server restart | Keep the base address in one setting of your script or system, not spread through code. |
| At most 100 MB per request (Cloudflare) | Split large uploads. |
| Requests longer than about 100 seconds are ended (Cloudflare) | Start long work in Odoo as a scheduled action or queue job; poll for the result. |
| Everyone with the link can reach the instance | Use a dedicated Odoo user with only the rights the integration needs, and an API key you can revoke. |

---

## 8. The panel's own API

To create, change, restart or delete instances from another system (a CI pipeline, a bot, a script), use the
panel's API at `/api/v1`, with an API token and a signature on every request: see [Panel API](PANEL_API.md).

The panel's other endpoints (`/api/...` without `v1`) serve the panel's own pages: they need a signed-in browser
session, a CSRF token and the panel's own origin, so they cannot be called from scripts or other sites.
