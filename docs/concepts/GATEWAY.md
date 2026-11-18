# The Gateway (Reverse Proxy)

**In one sentence:** the gateway is a small program that stands in front of every instance, passes each visit on to
Odoo, and because it sees every visit, it can notice when an instance is unused and wake it when someone comes back.

---

## Contents

1. [An everyday comparison](#1-an-everyday-comparison)
2. [What a reverse proxy is](#2-what-a-reverse-proxy-is)
3. [What our gateway does](#3-what-our-gateway-does)
4. [Example: a visit to a paused instance](#4-example-a-visit-to-a-paused-instance)
5. [Where it runs: its own container](#5-where-it-runs-its-own-container)
6. [Why Odoo needs proxy_mode](#6-why-odoo-needs-proxy_mode)
7. [Why we wrote our own](#7-why-we-wrote-our-own)

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

Well-known reverse proxies are nginx, Apache and Caddy. Ours is a few hundred lines of Python (`gateway.py`, built
on aiohttp), because it needs to do things those do not do on their own.

---

## 3. What our gateway does

There is one gateway port per instance, on `127.0.0.1` (on every interface with the `host-ports` exposure). The
instance's tunnel connector points at it. For every request:

| Step | Why |
| :--- | :--- |
| Records the time of the visit | Instances without visits for 6 hours are paused. |
| Removes the panel's cookie, if the browser sent one (in any of its `Cookie` headers), and stops Odoo from setting a cookie with that name | The panel's session must never reach an instance. |
| Removes hop-by-hop headers (`Connection`, `Upgrade`, `Transfer-Encoding`, ... and any header named in `Connection`), both ways | They describe one connection, not the request; passed on, they can confuse Odoo about where a request ends. |
| Appends the visitor's address to `X-Forwarded-For` and sets `X-Forwarded-Proto` and `X-Forwarded-Host` itself | Odoo believes the last address of `X-Forwarded-For`. That is the address Cloudflare reports (`CF-Connecting-IP`) or, with `host-ports`, the address the connection comes from: never one the visitor typed, so Odoo's sign-in limits per address cannot be dodged. |
| Forwards the request to the instance's Odoo, passing the body on piece by piece as it arrives, and the answer the same way | Large uploads and downloads never sit whole in memory. Bodies larger than `GATEWAY_MAX_UPLOAD_MB` are refused (`413`), and one that stops arriving for `GATEWAY_IDLE_TIMEOUT_SECONDS` is given up (`408`). |
| Closes a connection that has not sent its request headers within `GATEWAY_HEADER_TIMEOUT_SECONDS`, and answers `503` to new connections beyond `GATEWAY_MAX_CONNECTIONS` (this instance) or `GATEWAY_MAX_CONNECTIONS_TOTAL` (all instances) | Slow or idle connections cannot use up the gateway's open files. |
| Keeps WebSocket connections open both ways, each message up to `GATEWAY_WEBSOCKET_MAX_MESSAGE_MB` | Odoo's live updates (chat, notifications) work; one visitor cannot make the gateway hold a huge message. |
| Reuses its connections to Odoo (keep-alive) | No new connection per request. |
| If Odoo does not answer: asks the panel to start the instance (once), answers "Starting up" (`503`, `Retry-After: 4`); "Starting soon" while the panel cannot be reached, asking again every few seconds | A paused instance wakes on the first visit. |
| If an update is running: answers "Update in progress" | Visitors see a clear message instead of an error. |
| If the instance is switched off: answers "Switched off" | Nothing is started. |
| With custom domains (`CUSTOM_DOMAINS=acme`): answers them on ports 80 and 443, picks the certificate by the name the browser asks for, serves the instance only when `Host` is that same name, redirects http to https and answers the ACME checks on port 80 | An instance can live at `https://erp.client.com` ([Administrator Guide](../ADMIN_GUIDE.md#19-custom-domains-and-certificates)); a renewed certificate is used at the next connection, without a restart. |

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

## 5. Where it runs: its own container

The gateways run in their own container, `sudoo-gateway` (`GATEWAY_MODE=service`, the default), apart from the
panel's backend (`sudoo-panel`). Restarting or updating the panel then no longer takes the instances offline:

| While the panel is down | |
| :--- | :--- |
| A running instance | Answers as usual. Visits are recorded in `visits.json`; the panel takes them when it is back. |
| A paused instance | Answers "Starting soon"; the page asks again every 4 seconds, and the instance starts as soon as the panel is back. |
| A switched-off instance | Answers "Switched off". |
| Creating, deleting, settings | Wait for the panel. A change of a gateway setting reaches the gateway within 2 seconds of the panel being back. |

The gateway has no Docker access, no `.env` and none of the panel's data. It shares one folder with the panel,
`/opt/odoo/gateway`: the panel writes `routes.json` there (the instances, their ports, the gateway settings), the
gateway writes `visits.json`, and each serves a Unix socket for the other (the panel asks the gateway to reload its
routes and collects the request counts for the metrics; the gateway asks the panel to start a paused instance).
The [Architecture](../ARCHITECTURE.md#2-how-a-visit-reaches-odoo) page has the details.

**Why the gateway asks the panel to start an instance.** Starting needs Docker, and the Docker socket is root on the
server. Giving it to the one process that every visitor talks to would undo the point of separating it. Letting the
gateway start only the two containers of an instance would still be a way to Docker from the internet-facing
process, and would skip what the panel does first (apply the instance's current configuration and limits). So
waking waits for the panel; only paused instances are affected, and production instances never pause.

**When the gateway service is not running**, the panel serves the instances itself, exactly as it did before: at
once when the gateway stopped normally, after `GATEWAY_TAKEOVER_SECONDS` (10) when it stopped answering. Both open
the ports with `SO_REUSEPORT`, so a port changes hands without a moment where nobody listens, and the connectors
never notice. `GATEWAY_MODE=embedded` keeps the gateways in the panel for good.

---

## 6. Why Odoo needs proxy_mode

Without a proxy, Odoo sees the visitor directly. Behind the gateway, every request seems to come from `127.0.0.1`
over plain http. With `proxy_mode = True` in `odoo.conf`, Odoo reads the forwarded headers instead:

| Without `proxy_mode` | With `proxy_mode` |
| :--- | :--- |
| Every visitor's address is `127.0.0.1` (logs and security rules are useless) | The visitor's real address |
| Odoo thinks the visit is `http`, builds `http://` links and insecure cookies | `https://` links and secure cookies |

---

## 7. Why we wrote our own

| Option | Why not |
| :--- | :--- |
| nginx or Caddy | Cannot start a stopped Docker container by themselves, and do not tell us when an instance was last visited. |
| Keep every instance running | Not enough memory: about 1 to 1.5 GB per instance on a 3 GB server. |
| Odoo directly behind the tunnel | Same problem: a stopped Odoo cannot wake itself. |

Writing it with aiohttp keeps it small, streaming and able to hold many open WebSocket connections at once.
