# The Gateway (Reverse Proxy)

**In one sentence:** the gateway is a small program that stands in front of every instance, passes each visit on to
Odoo, and because it sees every visit, it can notice when an instance is unused and wake it when someone comes back.

---

## Contents

1. [An everyday comparison](#1-an-everyday-comparison)
2. [What a reverse proxy is](#2-what-a-reverse-proxy-is)
3. [What our gateway does](#3-what-our-gateway-does)
4. [Example: a visit to a paused instance](#4-example-a-visit-to-a-paused-instance)
5. [Why Odoo needs proxy_mode](#5-why-odoo-needs-proxy_mode)
6. [Why we wrote our own](#6-why-we-wrote-our-own)

---

## 1. An everyday comparison

A hotel receptionist stands between guests and rooms. Guests never walk into a room directly: they ask at the desk,
and the receptionist sends them up. Because every guest passes the desk, the receptionist knows which rooms are in
use, can say "one moment, the room is being prepared", and can refuse a room that is closed.

The gateway is that receptionist; each instance's Odoo is a room.

---

## 2. What a reverse proxy is

A **reverse proxy** receives requests meant for another program and forwards them to it, then passes the answer
back. The visitor only ever talks to the proxy.

![A reverse proxy](../images/reverse-proxy.svg)

Well-known reverse proxies are nginx, Apache and Caddy. Ours is about 200 lines of Python (`gateway.py`, built on
aiohttp), because it needs to do things those do not do on their own.

---

## 3. What our gateway does

There is one gateway port per instance, on `127.0.0.1`. The instance's tunnel connector points at it. For every request:

| Step | Why |
| :--- | :--- |
| Records the time of the visit | Instances without visits for 6 hours are paused. |
| Removes the panel's cookie, if the browser sent one, and stops Odoo from setting a cookie with that name | The panel's session must never reach an instance. |
| Passes on the visitor's address and `https` (`X-Forwarded-For`, `X-Forwarded-Proto`) | Odoo then knows who visits and that the visit is secure. |
| Forwards the request to the instance's Odoo, streaming the body | Large uploads and downloads do not fill memory. |
| Keeps WebSocket connections open both ways | Odoo's live updates (chat, notifications) work. |
| If Odoo does not answer: starts the instance once, answers "Starting up" (`503`, `Retry-After: 4`) | A paused instance wakes on the first visit. |
| If an update is running: answers "Update in progress" | Visitors see a clear message instead of an error. |
| If the instance is switched off: answers "Switched off" | Nothing is started. |

---

## 4. Example: a visit to a paused instance

```
 09:00:00  Visitor opens https://river-stone-apple.trycloudflare.com/odoo
 09:00:00  Gateway: Odoo is not answering, instance is paused → start it (once), answer "Starting up"
 09:00:04  Browser reloads the page by itself → still starting → "Starting up, waiting for 4 seconds"
 09:00:08  ...
 09:00:31  Browser reloads → Odoo answers → the visitor sees Odoo
```

An API client gets the same `503` with `Retry-After: 4` as plain text, and tries again.

---

## 5. Why Odoo needs proxy_mode

Without a proxy, Odoo sees the visitor directly. Behind the gateway, every request seems to come from `127.0.0.1`
over plain http. With `proxy_mode = True` in `odoo.conf`, Odoo reads the forwarded headers instead:

| Without `proxy_mode` | With `proxy_mode` |
| :--- | :--- |
| Every visitor's address is `127.0.0.1` (logs and security rules are useless) | The visitor's real address |
| Odoo thinks the visit is `http`, builds `http://` links and insecure cookies | `https://` links and secure cookies |

---

## 6. Why we wrote our own

| Option | Why not |
| :--- | :--- |
| nginx or Caddy | Cannot start a stopped Docker container by themselves, and do not tell us when an instance was last visited. |
| Keep every instance running | Not enough memory: about 1 to 1.5 GB per instance on a 3 GB server. |
| Odoo directly behind the tunnel | Same problem: a stopped Odoo cannot wake itself. |

Writing it with aiohttp keeps it small, streaming and able to hold many open WebSocket connections at once.
