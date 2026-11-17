# Security

**Audience:** administrators, and anyone reviewing the panel's security.
**Covers:** what is protected, against whom, how, and what is deliberately not protected.

---

## Contents

1. [What we protect, and from whom](#1-what-we-protect-and-from-whom)
2. [Reachability](#2-reachability)
3. [Panel passwords](#3-panel-passwords)
4. [Stored secrets: the vault](#4-stored-secrets-the-vault)
5. [Sessions and the browser](#5-sessions-and-the-browser)
6. [Checking every input](#6-checking-every-input)
7. [What is not protected](#7-what-is-not-protected)
8. [API tokens and webhooks](#8-api-tokens-and-webhooks)
9. [Roles and the audit log](#9-roles-and-the-audit-log)
10. [Instance containers](#10-instance-containers)
11. [Marketplace plugins: keys, signatures, licences](#11-marketplace-plugins-keys-signatures-licences)
12. [Checklist for the administrator](#12-checklist-for-the-administrator)
13. [Releases and signatures](#13-releases-and-signatures)

---

## 1. What we protect, and from whom

| What | Why it matters |
| :--- | :--- |
| The panel | It controls Docker on the server: a super admin or administrator account can do anything on the server. |
| Panel passwords | People reuse passwords; a leaked one could open other doors. |
| The GitHub App's private key, its installation tokens, and deploy keys | They read the customers' private code. |
| The server | It runs every instance. |

| From whom | Typical attempt | Main defence |
| :--- | :--- | :--- |
| Anyone on the internet | Guessing passwords on the sign-in page, or taking over a new server before its owner | Slow hashes, sign-in lockout, strong generated passwords, no default accounts, a one-time setup code |
| A website the user visits | Making the user's browser send requests to the panel | Same-origin check, CSRF token, JSON-only, `SameSite=Strict` cookie |
| Somebody typing into a form | Text that becomes a command, a path or a script | Allow-list checks, no shell, escaping, Content-Security-Policy |
| A person with a smaller role | Doing what their role does not allow | A permission checked on the server for every request |
| Someone with a copy of the files (a backup, a copied folder) | Reading passwords and keys | Hashes and encryption with a key kept apart |

Examples of each attempt, and how the panel stops it, are in [Web attacks](concepts/WEB_ATTACKS.md).

---

## 2. Reachability

* The panel is three containers. The web service (`sudoo-web`, Caddy) serves the web app on `PANEL_PORT` and forwards
  `/api`, `/hooks` and `/docs` to the backend (`sudoo-panel`). The backend's API (`API_PORT`) and its live event
  stream (`EVENTS_PORT`) listen on `127.0.0.1` only, with every exposure: nothing reaches them except through the
  web service. The third, the gateway service (`sudoo-gateway`), holds the instances' gateway ports (next section).
* With the default exposure plugin (`cloudflare-quick`), the web service, every gateway and every Odoo
  listen on `127.0.0.1` only. Nothing is reachable from the internet except through the Cloudflare connectors that
  the server opened itself, and SSH for administrators.
* With `host-ports`, the web service and the gateways listen on every interface, on purpose; Odoo itself and the
  databases still listen on `127.0.0.1` only. Use a firewall to limit who can reach the ports, and https in front of
  them on any network you do not fully trust (see [Plugins](PLUGINS.md#host-ports)).
* Cloudflare carries the traffic over https and decrypts it at its edge (that is how quick tunnels work); between
  Cloudflare and the server it travels inside the connector's own encrypted connection. See
  [Tunneling](concepts/TUNNELING.md#6-is-it-safe).
* The panel and every instance have different addresses (different browser origins), so an instance's pages can never read
  the panel's cookie. The gateway also removes the panel's cookie from requests to Odoo and stops Odoo from setting
  one with the same name.

### The gateway service

The gateways are the only part of the panel that every visitor of every instance talks to, so they run apart from
the backend (`GATEWAY_MODE=service`, the default) with as little as possible:

| | Gateway service (`sudoo-gateway`) | Backend (`sudoo-panel`) |
| :--- | :--- | :--- |
| Docker socket | not mounted | mounted (it manages the instances) |
| `.env`, vault key, accounts, sessions, backups | none: no `env_file`, no mount of `/opt/odoo` | all |
| Files | `/opt/odoo/gateway` only, read-write: routes from the backend, visits for it, the two sockets | `/opt/odoo` |
| Container | read-only file system, all capabilities dropped, `no-new-privileges`, at most 256 processes | as before |

The two talk through Unix sockets in that folder (mode `700`, root only), never over the network: `gateway.sock`
(the backend asks the gateway to reload its routes and collects the request counts) and `panel.sock` (the gateway
asks the backend to start a paused instance, naming it; the backend checks the name and decides, through the same
job as a start from the panel). A process that can open those sockets is root on the server already, so they need
no further secret; the instances' containers have no access to the folder. Everything the gateway reads from
`routes.json` is checked (instance names, ports, a known listen address and header name, only the gateway's own
settings), and everything the backend reads from the gateway (visits, counts, errors) is checked and limited to known
instances. Someone who took over the gateway service could read and change visitors' traffic to the instances (as
any reverse proxy could), but would not reach Docker, the panel's data or its secrets.

### The visitor's address

Sign-in lockout, rate limits and the audit log count by the visitor's address, so it must not be something a
visitor can make up:

| Source | Trusted when |
| :--- | :--- |
| `CF-Connecting-IP` | Only with the `cloudflare-quick` exposure, where Cloudflare sets it (the exposure plugin's `client_ip_header`). With `host-ports` it is ignored. |
| `X-Forwarded-For` | Only when the request reaches the backend from `127.0.0.1`, that is from the web service, which replaces any value sent by the visitor with the address it received the request from. |
| The connection's own address | In every other case. |

Odoo, behind the gateway, runs in proxy mode and believes the **last** address in `X-Forwarded-For`. The gateway
appends the address it can vouch for (`CF-Connecting-IP` with `cloudflare-quick`, the connection's own address with
`host-ports`) and sets `X-Forwarded-Proto` and `X-Forwarded-Host` itself, so a visitor cannot choose the address
Odoo's own sign-in limits count. It also removes hop-by-hop headers (and any header named in `Connection`) in both
directions, refuses request bodies larger than `GATEWAY_MAX_UPLOAD_MB`, gives up on bodies that stop arriving
(`GATEWAY_IDLE_TIMEOUT_SECONDS`) and limits WebSocket messages (`GATEWAY_WEBSOCKET_MAX_MESSAGE_MB`), so one visitor
cannot make the gateway that every instance shares hold gigabytes in memory. A connection that does not send its
request headers within `GATEWAY_HEADER_TIMEOUT_SECONDS` is closed, and the number of connections open at once is
capped per instance (`GATEWAY_MAX_CONNECTIONS`) and for all gateways (`GATEWAY_MAX_CONNECTIONS_TOTAL`), so slow or
idle connections cannot use up the gateway's open files. The web service gives a request 10
seconds for its headers and 30 for its body, and refuses bodies over 1 MB for the backend. See
[Performance](PERFORMANCE.md#5-security-on-the-hot-paths).

---

## 3. Panel passwords

Passwords are **never stored**. The panel only needs to *check* a password, so it keeps a one-way **hash**:

```
stored = scrypt( HMAC-SHA256(pepper, password), salt )      salt: 16 random bytes per password
                                                             pepper: derived from vault.key
                                                             scrypt: N = 2^15, r = 8, p = 1 (32 MB, about 0.1 s)
```

| Measure | Effect |
| :--- | :--- |
| **scrypt** | Slow and memory-hungry on purpose: a stolen hash costs an attacker about 0.1 s and 32 MB per guess, so millions of guesses become impractical. |
| **Salt** | Two people with the same password have different hashes; precomputed tables are useless. |
| **Pepper** | A secret mixed in before hashing, taken from `vault.key`, which is not in the database (`sudoo.db`). A stolen database alone cannot even be attacked. |
| **Constant-time comparison** | Checking a password takes the same time whether it is almost right or completely wrong, and for unknown accounts too: timing reveals nothing. |
| **Generated passwords** | When an administrator chooses **Generate** for a new account or a new password, the panel makes 24 random characters (144 bits), shown once. They must be replaced at the first sign-in (see below). |
| **Passwords typed by an administrator** | Checked against the same rules as any chosen password. By default they must also be replaced at the first sign-in; the administrator can turn that off. |
| **Rules for chosen passwords** | Follow NIST SP 800-63B: length and a blocklist, no composition rules ([below](#password-rules)). Checked on the server and, as you type, in the browser. |
| **Sign-in by e-mail address** | People sign in with their e-mail address (or their user name). Each e-mail address belongs to one account at most, also when people change their own address in **My account** (compared without case). A wrong address and a wrong password give the same answer, "Wrong e-mail address or password.", and take as long: an unknown address still runs a full password check. |
| **Sign-in lockout** | 5 failures for an account from one address, or 20 from one address, block sign-in for 15 minutes. An e-mail address counts as its account, so switching between the address and the user name does not give more tries. |
| **Changing or resetting a password** | Ends every other session of that account. |
| **Upgrade on sign-in** | Hashes made before the pepper existed are replaced with the current kind the next time their owner signs in. |

Why a hash and not encryption is explained in [Passwords and encryption](concepts/PASSWORDS_AND_ENCRYPTION.md).

### Password rules

The rules follow NIST SP 800-63B (Digital Identity Guidelines, memorized secrets): a long password that is not on a
list of known ones protects better than forced mixes of characters, which lead to predictable patterns such as
`Password1!`. The same rules apply on the setup page, for new accounts, for passwords set by an administrator and
in **My account**, on the server and in the browser.

| Rule | Detail |
| :--- | :--- |
| Length | 12 to 128 characters. |
| Characters | Any printable character, spaces included. No control characters. |
| No composition rules | Capitals, digits and symbols are never required. Passwords never expire. |
| Blocklist | Refused when it contains a common password or word: `password`, `qwerty`, `admin`, `odoo`, `sudoo`, `welcome` and others. |
| Sequences | Refused when it contains five characters in a row of the alphabet, the digits or a keyboard row, in either direction (`abcde`, `12345`, `54321`, `qwerty`, `asdfg`, `zxcvb`). |
| Repeats | Refused when one character appears four times in a row. |
| Variety | At least 6 different characters. |
| Personal details | Refused when it contains the person's first name, last name, user name or the part of the e-mail address before `@`, each counted from 3 characters. |
| Paste and password managers | Allowed. |

The browser shows a strength meter of four bars (**Acceptable**, **Good**, **Strong**, **Very strong**); it only
guides, the server decides. The full list of rules for people is in the
[User manual](USER_MANUAL.md#password-rules).

### First-run setup

There are **no default accounts**: a new server has no account at all, so there is no known password to try.

* While no account exists, the panel makes a one-time **setup code** (three groups of four letters and digits, for
  example `K7QH-3MXP-9TAD`) and keeps it in `/opt/odoo/manager/setup_code`, mode 600 (root only). The installer
  prints it; `docker exec sudoo-panel sudoo.sh setup-code` shows it again.
* The **Set up Sudoo.sh** page (`POST /api/v1/setup`) creates the first account, a **Super admin**, only with that
  code, and signs in. It asks for an e-mail address (the user name is made from it) and a password that follows
  the [password rules](#password-rules). The code is compared in constant time and deleted once the account exists; after that the
  setup page refuses every request.
* Wrong codes are throttled: 5 from one address in 15 minutes block that address for 15 minutes, and each one is
  written to the audit log.
* Someone who reaches the address before you still needs the code, which only a person with root on the server can
  read.

### Generated passwords must be replaced

A password chosen by someone else (a new account, or a new password set by an administrator) is known to two
people. The account is therefore marked (always for a generated password; for a typed one unless the administrator
turned off **Ask them to choose their own password when they first sign in**): after signing in with it, the session is **restricted**. It opens only the session
and **My account** endpoints (`/api/v1/session`, `/api/v1/account`, `/api/v1/contact`); everything else answers
403 "Choose your own password first." until the person has chosen a new password. The same restriction, with "Set
up a second factor first.", applies when `REQUIRE_SECOND_FACTOR` requires a factor the account does not have yet.

### Second factors

A password can leak; a second factor means a leaked password alone does not open the panel.

| Factor | How it works |
| :--- | :--- |
| **Authenticator app** (`totp`) | The app and the panel share a secret (stored encrypted with the vault key) and both compute a 6-digit code every 30 seconds (RFC 6238). Each code works once; codes of the step before and after are accepted for clocks that are slightly off. |
| **E-mail code** (`email`) | A 6-digit code sent from the panel's mailbox (`SMTP_*`), valid 10 minutes, used once, kept only as a hash; 5 wrong tries use it up; a new one can be asked for once a minute. Setting it up confirms the address with a code. |

* After the password, the session only remembers who passed the first step, for 5 minutes; it opens nothing.
* Wrong codes count like wrong passwords (5 per account and address in 15 minutes, then blocked).
* `REQUIRE_SECOND_FACTOR` (`admins` by default) makes it compulsory: such an account without a factor can only set
  one up after signing in. Removing a factor, or replacing one that is set up, needs the password and ends the
  account's other sessions. A new sign-in e-mail address (in the profile or through the e-mail code) ends them too.
* A lost phone: an administrator removes the account's factors (**Accounts**, or
  `DELETE /api/v1/users/<name>/second-factors`), which signs it out everywhere.
* More factors (SMS, hardware keys) can be added as plugins ([Plugins](PLUGINS.md#8-more-plugin-types)).

---

## 4. Stored secrets: the vault

Some secrets must be **read back** by the panel, so they cannot be hashed. They are **encrypted**:

| Secret | Stored as | Read when |
| :--- | :--- | :--- |
| GitHub deploy keys (`keys/deploy/*.key`) | AES-256-GCM, label `deploy-key:<owner>/<repo>` | For the length of one git connection, decrypted into a memory-only file readable by root, then deleted |
| The GitHub App's private key, webhook secret and client secret (`keys/github-app.json`, mode 600) | AES-256-GCM, labels `github-app:pem`, `github-app:webhook_secret`, `github-app:client_secret` | The private key to sign a short JWT when a new installation token is needed; the webhook secret to check each delivery; the client secret is kept but not used |
| GitHub App installation tokens (read-only, one hour) | AES-256-GCM in a file in `MEMORY_DIR` (memory only, mode 600), label `github-app-token:<installation>` | When git needs one (through the panel's credential helper) and for API calls; reused until five minutes before they expire |
| Odoo master password of each instance (`MASTER_PASSWORD` in its `.env`) | AES-256-GCM, label `master-password:<name>` | When someone with the permission `instances.credentials` opens an instance's **Access** tab (and the audit log records it) |
| Secret settings changed in the panel (`smtp_password`, `odoo_admin_password`, `github_webhook_secret`) | AES-256-GCM in `/opt/odoo/manager/settings.json`, label `setting:<key>` | When the panel starts, and when a setting is changed |
| API tokens' signing secrets | AES-256-GCM | On every signed API call |

Backups (`/opt/odoo/backups`) contain whole databases, so they are mode 600 (root only), listed and downloaded only
with the permission `backups.manage` (super admins and administrators), and only by their file name from the
listing, never by a path. An instance's page lists its own backups to everyone who may see the instance, without
the download link.

Restoring a backup replaces an instance's database and files, so it has its own permission, `instances.restore`
(super admins, administrators and maintainers). With it alone, a restore only takes a backup of that same instance.
"Same" means the instance that exists now, not an earlier, deleted instance that had its name: each instance gets a
random `INSTANCE_ID` when it is created, recorded in every backup's `manifest.json`, and a backup is its own only
when the two match (a backup taken before the id was recorded counts when it was taken after the instance was
created). A deleted instance's backups therefore need `backups.manage` even when its name is reused, are never
picked by "Restore the latest backup", and are neutralised when they are put back.
Putting another instance's backup into an instance, or starting a new instance from a backup, copies one instance's
data into another, so it also needs `backups.manage`, the permission that already lets a role see and download every
backup (super admins and administrators): a maintainer cannot use it to read an instance's data through another one.
The backup is always chosen by file name from the backup folder, never as a path, and must be of the target's Odoo
series. Copied data is neutralised (no outgoing e-mail, no scheduled actions, Odoo's integrations off, a new
`database.secret` so sessions and signed links of the original do not work in the copy). Every restore first saves
the current state as a `before-restore` backup, offered as **Rollback** for `ROLLBACK_HOURS`: a restore by mistake
can be undone ([Administrator guide: Backups](ADMIN_GUIDE.md#10-backups)).

* **AES-256-GCM** is authenticated encryption: besides hiding the value, it detects any change to it. A modified
  value is refused, never decrypted into garbage.
* Every value is encrypted together with a **label** saying what it is. A value copied to another place (for
  example one repository's key renamed as another's) fails to decrypt.
* The key is `/opt/odoo/manager/vault.key`: 32 random bytes, created on first use, mode 600. It is kept apart from
  the data: a copy of the instance folders or of the deploy keys alone is useless. Back it up **separately**
  ([Administrator guide: Backups](ADMIN_GUIDE.md#10-backups)).
* Odoo itself receives only a **hash** of its master password in `odoo.conf` (Odoo's own `pbkdf2-sha512` format).
  Odoo can check it but nobody can read it from that file.
* The cookie-signing key (`secret_key`) and the vault key are generated on the server and never leave it.

### Settings changed in the panel

Super admins change almost every setting on the **Settings** page (`PATCH /api/v1/settings`, permission
`settings.manage`). The changes are kept in `/opt/odoo/manager/settings.json` (mode 600) on top of `.env`.

* The three secret settings (`SMTP_PASSWORD`, `ODOO_ADMIN_PASSWORD`, `GITHUB_WEBHOOK_SECRET`) are **write-only**: the
  page and the API only say whether they are set, never their value. In `settings.json` they are encrypted with the
  vault key; without `vault.key` they cannot be read back.
* Folders, ports, the exposure and the storage can only be set in `.env`, which only root on the server can edit.
* Every change is in the audit log with the names of the settings. Secret values are not written there: a changed
  secret appears as `SMTP_PASSWORD=(set)`. Versions before this one wrote the value; the panel replaces those
  values with `(set)` once, the first time it starts after the update. If administrators or `write` API tokens
  could read the audit log before, change the mail password and the webhook secret.

---

## 5. Sessions and the browser

| Measure | Effect |
| :--- | :--- |
| Signed session cookie `sudoo_session` | `HttpOnly` (scripts cannot read it), `Secure` (https only), `SameSite=Strict` (never sent with requests started by another site). |
| Session limits | Ends after 60 minutes without activity and after 12 hours in any case. |
| Session version | Each account has a random version number in its record, drawn again on a password change or reset, a replaced or removed second factor, a new sign-in e-mail address and every other event that ends its sessions; every older session then stops working at once. Because it is random, an account created later under the same user name never accepts a session of a deleted one. |
| Signing out | Each session has a random id. **Sign out** records it as ended on the server (until the session would have expired anyway), so a copy of the cookie taken before stops working. |
| Live events | The event stream checks its caller every minute and ends when the session was signed out or ended, the account deleted or the API token revoked. One account or token holds at most 8 streams at once, so no one caller can take the 500 the panel allows. |
| CSRF token | Every change must carry the session's secret token in a header, which another site cannot know. |
| Same-origin check | Every change must come from the panel's own address (`Origin` header). |
| JSON only | Every change must be sent as `application/json`; a form posted from another site cannot do that. |
| Content-Security-Policy | Set by the web service (`web/Caddyfile`) on the web app, and by the backend on its own answers: only the panel's own scripts and styles run; no inline scripts or styles, no outside resources, no framing. An injected `<script>` would not run. |
| Other headers | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`, `Permissions-Policy` (no camera, microphone, location or payment); on API answers also `Cross-Origin-Resource-Policy: same-origin`, `Cache-Control: no-store`, and `Strict-Transport-Security` when the exposure uses https. |
| Escaping | The web app is built with React, which inserts every value as text, never as HTML. The only HTML it shows is the documentation, rendered on the server from the project's own Markdown files. |
| One API | The web app uses only `/api/v1`, the same API as other systems, with the same checks. The pages and internal API of earlier versions are removed. |

---

## 6. Checking every input

Every value received from a browser or API client goes through `sudoo/web/validation.py` before it is used:

| Rule | Example of what is refused |
| :--- | :--- |
| The request body is a JSON object, at most 64 KB | A form post, a JSON list, a 1 MB body |
| Every field has the expected type | `"instance": "yes"` instead of `true`; a number where text is expected |
| Text is trimmed, limited in length, without control characters | A name of 500 characters; a NUL byte or a line break inside a branch name |
| Names, addresses and branches match an allow-list pattern | `https://evil.example/x` as a GitHub address; `../../etc` as a folder |
| Branch names cannot look like command options or path tricks | `--upload-pack=...`, `-rf`, `a..b`, `x.lock` |
| Choices must be one of the offered values | Odoo version `99.0`; role `superuser` |
| Instance and user names in addresses are checked before any file is touched | `/api/v1/instances/..%2F..%2Fetc` |

After checking, values are only passed to commands as **separate arguments, never through a shell**
(`core/shell.py`, `core/jobs.py`), and git commands put `--` before addresses, so nothing typed can become a command or an
option. Refused input gets HTTP 400 with a sentence saying what to enter instead.

---

## 7. What is not protected

Being clear about limits is part of security.

| Not protected | Why, and what to do |
| :--- | :--- |
| **Instance contents** | Every instance opens with `admin` / `admin` and anyone with its link can sign in. Instances must not hold real customer data. |
| **Someone with root on the server** | Root can read `vault.key` as well as the encrypted values, and controls Docker. The vault protects copies and backups, not a compromised server. Keep SSH access to the server limited. |
| **A super admin or administrator account** | The panel controls Docker, so these roles can do anything on the server. Give them only to people who would be trusted with the server. |
| **Plain http with `host-ports`** | With `PUBLIC_SCHEME=http`, passwords and session cookies travel unencrypted (the cookie is then not marked Secure). Put a TLS proxy in front, or keep it to trusted networks. |
| **Traffic inside Cloudflare** | Quick tunnels end https at Cloudflare's edge. For a server this is acceptable; Tailscale Funnel is the end-to-end alternative ([Tunneling](concepts/TUNNELING.md#7-other-ways-and-why-we-chose-this-one)). |
| **Each instance's database password** | Stored readable in the instance's `.env`, `docker-compose.yml` and `odoo.conf` (folder mode 700, root only; `odoo.conf` readable by the Odoo container's user only). The database is reachable only from that instance's Odoo container, on an internal Docker network that the server itself has no address on (Docker 28 and later), so the password is of no use to anyone who could not already read the whole server. |
| **Root and the docker group** | Anyone who is root on the server, or in its `docker` group, can change, replace or enter every container and file; nothing inside the panel can prevent that. The panel makes such changes visible and undoes them on instances ([Instance containers](#10-instance-containers)); it cannot stop them. |
| **Sign-in lockout after a restart** | Failed attempts are counted in memory; a panel restart resets the counters. The slow hash still limits guessing. |
| **Odoo's own security** | Odoo inside an instance is the official image, unchanged; its own security is Odoo's. |
| **A plugin's files, from root** | Installed plugins are in `/opt/odoo/manager/plugins/` (root only, never served, no API returns them), but root on the server can read them. Their code is protected by the panel never exposing it and by the licence forbidding copies, not by encryption ([Marketplace plugins](#11-marketplace-plugins-keys-signatures-licences)). |
| **What a plugin does once loaded** | A plugin runs inside the panel's process with the panel's rights. The marketplace's review, the permissions it must declare and the hub's signature are the safeguards; install only the plugins you need. |

---

## 8. API tokens and webhooks

| Entry point | Who can use it | How it is checked |
| :--- | :--- | :--- |
| `/api/v1` (other systems) | holders of an API token **and** its signing secret | token (SHA-256 hash), HMAC-SHA256 signature over timestamp, nonce, method, path and body; timestamp within 5 minutes; each nonce accepted once; scope `write` acts as an Administrator, `read` as a Viewer, `instances` as a Connected app limited to the endpoints a connected app needs ([Panel API](PANEL_API.md#the-instances-scope)); an optional `X-Sudoo-Acting-For` is signed too and names the person in the audit log; 5 refused calls from an address block it for 15 minutes |
| `/hooks/github` (when enabled) | GitHub | `X-Hub-Signature-256`: HMAC-SHA256 of the body with `GITHUB_WEBHOOK_SECRET` |
| `/hooks/github-app` (when enabled) | GitHub, for the panel's GitHub App | `X-Hub-Signature-256`: HMAC-SHA256 of the body with the app's webhook secret (created by GitHub, stored encrypted); 404 while no app is connected |

### The GitHub App

The panel's GitHub App ([Administrator guide](ADMIN_GUIDE.md#15-the-github-app)) is built for least privilege:

* **Read-only.** The manifest asks for `contents: read` and `metadata: read` only, and every installation token is
  requested with exactly those permissions. The app is private (`public: false`). Installations can be limited to
  selected repositories on GitHub.
* **Only super admins** (`settings.manage`) connect, install, refresh and disconnect it; roles with `github.manage`
  only see its state. Every step is in the audit log.
* **The manifest round trip is bound to the session.** Starting a connection stores a random 256-bit `state` in the
  super admin's signed session, valid one hour; GitHub sends it back with the one-time code, and the panel exchanges
  the code only when the state matches, once. The code itself is useless after one hour or one use. An
  installation id sent back by GitHub is recorded only after GitHub confirms, with the app's JWT, that it belongs to
  this app.
* **Short-lived credentials.** The JWT (RS256) is valid nine minutes and issued 60 seconds in the past; installation
  tokens are valid one hour.
* **Tokens never touch disk in clear, addresses, `.git/config` or logs.** git asks the panel's credential helper
  (`credential.https://github.com.helper`, set through git's environment, with other helpers switched off so no
  helper can store a token) and receives the token on a pipe. Repository addresses stay `https://github.com/...`.
  Errors shown to people carry GitHub's message, never a token.
* **Webhook address.** The panel changes only the URL of the app's webhook (`PATCH /app/hook/config`) when its own
  public address changes; the secret never leaves the server.
* **Disconnecting** deletes the stored credentials, installations and cached tokens; the panel then links to
  GitHub's pages to uninstall and delete the app, which only the account's owner can do.
* The web app's Content-Security-Policy allows forms to be posted to `https://github.com` (`form-action`), needed
  to send the manifest; nothing else from GitHub is loaded or allowed.

Every caller is also limited in requests per minute (`RATE_LIMIT_READS_PER_MINUTE`, `RATE_LIMIT_CHANGES_PER_MINUTE`,
per user, token or address), and instance creation can be limited in number and frequency (`MAX_INSTANCES`,
`MAX_INSTANCES_PER_USER`, `CREATE_COOLDOWN_MINUTES`); see [Configuration](CONFIGURATION.md#limits).

The signing secret is stored encrypted (vault), the token only as a hash; both are shown once. API tokens do not open the
panel's pages; the panel's session opens the API only from the panel's own address, with its CSRF token. Details: [Panel API](PANEL_API.md#6-security-notes).

---

## 9. Roles and the audit log

Every account has one of five roles. A role is only a named set of **permissions**, and every check on the server
asks for a permission, never for a role: each endpoint, and each action on an instance, names the permission it
needs ([Panel API](PANEL_API.md#3-endpoints)). Hiding a button in the web app is only a convenience; the server
refuses the call anyway, with 403 naming the missing permission, and the refusal is in the audit log.

| Permission | Allows | Super admin | Administrator | Maintainer | Developer | Viewer |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| `instances.view` | See instances, their figures and activity | yes | yes | yes | yes | yes |
| `instances.logs` | Read Odoo's log and jobs' technical logs | yes | yes | yes | yes | yes |
| `instances.update` | Get new code, update submodules, restart, start, add submodules | yes | yes | yes | yes | |
| `instances.operate` | Pause, switch off, upgrade modules, switch branch, back up, remove submodules, change automation, make the lifetime longer | yes | yes | yes | | |
| `instances.create` | Create instances | yes | yes | yes | | |
| `instances.restore` | Restore an instance from a backup (replaces its database and files) | yes | yes | yes | | |
| `instances.delete` | Delete instances, also by giving one a shorter lifetime | yes | yes | | | |
| `instances.credentials` | See Odoo passwords and ports | yes | yes | yes | | |
| `backups.manage` | Download and delete backups | yes | yes | | | |
| `github.manage` | Manage deploy keys; see the GitHub App's state | yes | yes | yes | | |
| `accounts.manage` | Create, change and delete accounts | yes | yes | | | |
| `tokens.manage` | Create and revoke API tokens | yes | yes | | | |
| `audit.view` | Read the audit log | yes | yes | | | |
| `plugins.view` | See installed plugins, the marketplace and purchases | yes | yes | | | |
| `settings.manage` | Change the panel's settings, install and buy plugins, and restart it | yes | | | | |

* Only a **super admin** creates, changes, resets or deletes super admin accounts. The last super admin cannot be
  deleted or given another role, so there is always at least one.
* An administrator may edit their own account on the Accounts page, role included, but cannot delete it or set its
  password there (that needs the current password, in **My account**).
* Changing an account's role ends its sessions, your own included; the new permissions apply from the next sign-in.
* No two accounts share an e-mail address, so an address always signs in as exactly one account.
* **API tokens** have no account: a `write` token acts as an **Administrator**, a `read` token as a **Viewer**, an
  `instances` token as a **Connected app**, a role only tokens can have: `instances.view`, `instances.create`,
  `instances.update`, `instances.operate` and `instances.delete`, further limited to the calls a connected app
  needs: listing, creating (not from a backup), starting, pausing, restarting, backing up, extending and deleting
  instances ([Panel API](PANEL_API.md#the-instances-scope)).
* Roles without `instances.credentials` never receive passwords, master passwords or ports, even in API answers.
* Accounts of earlier versions were renamed once, when the panel started: `admin` became Super admin, the sync
  operator became Developer.
* The audit log (in `/opt/odoo/manager/sudoo.db`, shown under **Audit log**) records sign-ins, failed sign-ins,
  lockouts, wrong setup codes, refused actions, every action on an instance, viewed credentials, settings changes,
  and account and key changes, with time, account, address and result. Passwords are never written to it.

---

## 10. Instance containers

Each instance is two containers, `odoo` and `db` (PostgreSQL), plus a Cloudflare connector with the
`cloudflare-quick` exposure. They exist to run Odoo for visitors who come through the panel's gateway, and nothing
else. Three things follow from that: they get the least they need, there is no way into them except through the
panel, and a change made to them by hand is found, reported and undone.

**What is protected, and how**

| Protection | Odoo | PostgreSQL | Connector (new ones) |
| :--- | :--- | :--- | :--- |
| Linux capabilities | none (it runs as the image's `odoo` user from the start) | only `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETGID`, `SETUID` (the image's entry point prepares its folders as root, then becomes `postgres`) and `KILL` (so the init process can pass stop signals to PostgreSQL) | none |
| `no-new-privileges` | yes | yes | yes |
| Read-only system files | yes; writable: the `odoo-data` volume (`/var/lib/odoo`) and an in-memory `/tmp` | yes; writable: the `db-data` volume and in-memory `/var/run/postgresql` and `/tmp` | yes, with an in-memory `/tmp` |
| Folders from the server | `config/` (odoo.conf), `addons/` (the repository) and the Enterprise add-ons, all read-only | none | none |
| Network | the instance's own network (way out to the internet, for Odoo's own services) and its internal network to the database | the internal network only: no published port, no way out, unreachable from other instances and (Docker 28 and later) from the server | the server's network (it reaches the gateway on `127.0.0.1`) |
| Published ports | `127.0.0.1:<ODOO_PORT>` only | none | none |
| Limits | processes (`CONTAINER_PIDS_LIMIT`), memory (`ODOO_MEMORY_LIMIT`), processors (`ODOO_CPU_LIMIT`) | `CONTAINER_PIDS_LIMIT`, `DB_MEMORY_LIMIT`, `DB_CPU_LIMIT` | 256 processes, 256 MB |
| Logs | rotated (`CONTAINER_LOG_MAX_SIZE`, `CONTAINER_LOG_MAX_FILES`) | rotated | rotated (10 MB, 3 files) |
| Init process, restart policy | yes, `unless-stopped`, set by the panel | the same | the same |
| Image | pinned by digest when the instance is created; changes only with **Update Odoo and PostgreSQL images** | the same | `CLOUDFLARED_IMAGE` |
| Docker socket | never mounted | never mounted | never mounted |

These were checked by running the official images: Odoo 16.0, 17.0, 18.0 and 19.0 start, create databases,
install modules and print PDF reports with read-only system files and no capabilities.

The compose file is rendered by the panel from a fixed list of keys (`odoo.compose_spec`). Before it is written, the
panel refuses any rendering with another service or key, more capabilities, a port that is not on `127.0.0.1`, the
Docker socket or a system folder, or a writable folder from the server; neither a plugin nor a setting can add one.
Folders from the server are an allow-list: the instance's own folder, and at the Enterprise mount point only a folder
of Enterprise add-ons (one `ENTERPRISE_PATH` names, a version folder of the `git` source, or one the Enterprise
source returned). Values are written into the file literally, and the panel runs Compose with a fixed project name
and without the `.env` file, so editing `.env` cannot change an image, a port, a password, the edition, the
Enterprise folder or the project. The Enterprise folder and the edition are recorded when the instance is created
and come from that record from then on, an empty folder (Community) included.

The panel writes `.env` one key per line: it refuses a key that is not an upper-case name and a value with a line
break or another control character. Values it reads from an instance, such as the login of the Odoo administrator
after a restore, are only recorded when they are a single short line.

The instance folder is mode 700 and owned by root; `.env` and `docker-compose.yml` are mode 600; `config/` and
`odoo.conf` are mode 700 and 600 and owned by the Odoo image's user, so Odoo (which gets the folder read-only) can
read its configuration and no other user on the server can.

**Only through the panel.** Nothing of an instance listens anywhere but `127.0.0.1`; visitors reach Odoo through the
panel's gateway, and people change instances through the panel's jobs, each checked against a permission and written
to the audit log. A restore's filestore step runs one short-lived root container with only `CHOWN`, `DAC_OVERRIDE`
and `FOWNER` (the database name is checked against the names the panel gives and passed to it as an argument, never
as part of its script); everything else the panel does inside containers (`docker exec` of `psql`, `pg_dump`, `du`) runs with
the container's own limits.

**Changes made by hand.** When the panel starts an instance it records what it applied (the values that shape the
containers, the files it wrote and their SHA-256, the images). Every 20 seconds the monitor compares each instance
that has no job running with that record:

* `docker-compose.yml`, `config/odoo.conf` and `.env`: their hashes and their modes;
* each container, through `docker inspect`: image, capabilities, privileged mode, read-only system files, security
  options, user, mounts, networks, published ports, environment, labels, limits, restart policy, log rotation and
  init process;
* containers that carry the instance's label (`sh.sudoo.instance`) or Compose project but are not the panel's;
* the connector: no privileges, mounts, added capabilities or ports.

A difference seen twice in a row goes to the audit log ("configuration changed outside the panel"), shows as a banner
on the instance's page and is e-mailed to every super admin with an address. With `DRIFT_ACTION=restore` (the
default) the panel then runs **Restore the panel's configuration** as soon as no other job runs on the instance: it
backs up the database first when its container is affected (kind `before-repair`), writes its own compose file and
`odoo.conf` back, stops strange containers (they are kept, stopped, for whoever looks into it) and recreates the
affected containers. A paused instance stays paused. Before any Compose command that creates or starts containers,
the panel also puts its own files back, so it never applies a file it did not write.

What the panel never does on its own:

* recreate a connector (its public link would change): connector findings are only reported;
* take `.env` back: only a person can tell which of its values are right. The values that shape the containers come
  from the panel's record anyway; **Restore the panel's configuration** accepts the rest of the file as it is;
* restart a running instance to apply new hardening or new limits: instances made before this version, and changed
  limits, are applied the next time the instance starts or restarts through the panel.

**What cannot be protected.** Root, and anyone in the `docker` group, can do everything: stop the panel, change its
record, edit files and containers between two checks, or run `docker exec` into a container and act inside it with
that container's rights. Hardening limits what such a change can do inside a container, and the checks make it
visible and short-lived; they do not stop it. Keep the `docker` group empty except for administrators trusted with
root, and keep SSH access limited.

---

## 11. Marketplace plugins: keys, signatures, licences

Plugins from the Sudoo.sh marketplace are checked by the panel itself before they run
([Marketplace](MARKETPLACE.md)).

| Key or value | Kind | Where it is kept | What it does |
| :--- | :--- | :--- | :--- |
| Installation key | Ed25519 private key, made on the server | The panel's database, encrypted with the vault key (label `hub-identity:<id>`) | Signs every request to the hub (method, path, installation id, time, single-use nonce, body hash), so the hub knows which panel asks and nobody can replay or alter a request |
| Hub keys | Ed25519 public keys | The panel's database, starting from the root keys (`HUB_PUBLIC_KEYS`, else the keys the release ships for hub.sudoo.sh; for another hub, the keys it lists the first time) | Verify every answer, lease, command and package. A key the hub lists as `active` is accepted, a `retiring` one until its `not_after`; a `revoked` or `expired` one is dropped at once. A new key is trusted only when its endorsement, signed by a key already trusted and not revoked, verifies (`sudoo-hub-key-v1`, key id, public key, `not_before`) |
| Answer signature | Ed25519, header `Sudoo-Signature: kid=, ts=, sig=` over `sudoo-hub-response-v1`, the request's method, path, installation id and nonce, the status, the time and the body's SHA-256 | Checked on every answer, refusals and downloads included (a download over its `X-Sudoo-Package-SHA256`) | An answer without a valid signature from an accepted key, or more than 5 minutes from the server's clock, is not believed: it counts as "the marketplace cannot be reached". A changed or replayed answer (another request's nonce) fails |
| Lease | JWT signed by the hub (EdDSA only; `none` and HMAC algorithms are refused), for this installation, 72 hours | The panel's database | Says which plugins may run, the licence dates of the paid ones (with `run_until`) and the flags; only verified claims are used |
| Commands | Ed25519 over `sudoo-hub-command-v1`, id, installation id, kind, time and the SHA-256 of the canonical JSON payload | Carried out once, recorded in the panel's database | `remove_paid_plugins` (after the server is logged out of its account): back up, remove the listed paid plugins, acknowledge. A command whose signature does not verify is ignored |
| Hardware fingerprint | SHA-256 of `sudoo-fingerprint-v1`, the installation public key, the host's `/etc/machine-id` (mounted read-only) and the primary MAC address | Sent at registration and in every heartbeat; the hub keeps a keyed digest | A copy of the panel's data on another machine is flagged: its paid licences are withheld until the account owner accepts the machine or revokes the key |
| Package signature | Ed25519 over `sudoo-plugin-v1`, id, version and SHA-256 | Sent with the package | Binds a package to one plugin and one version; checked with an accepted hub key before anything is unpacked |
| Plugin file hashes | SHA-256 over every unpacked file | The plugin's record | Checked at every start and every 10 minutes; a changed plugin is not loaded and the super admins are e-mailed |

* Every request to the hub is signed, so every answer is signed back; downloads use a signed request, and a redirect
  to a storage link (itself signed) is followed only over https, without the signature.
* Packages with links, special files, absolute or `..` paths, compiled code, archive bombs or Python dependencies are
  refused; plugins are unpacked into `/opt/odoo/manager/plugins/` (folders 700, files 600) and the package file is
  deleted. The web container does not mount that folder.
* No endpoint returns a plugin's files or package. Payment links and receipts reach the browser only when they are
  https. Plugin icons are fetched by the panel (signed) and served from its own address, PNG or SVG only, with a
  CSP that runs nothing.
* **The panel never charges anything.** Buying opens a checkout session; the account owner pays on app.sudoo.sh.
  Card details never pass through the panel. Each press of **Buy** carries a single-use idempotency key, so a retried
  request never opens a second purchase.
* A paid plugin stops at its `run_until` (the last lease's end plus 96 hours for an active licence, never after the
  licence's grace), when its licence ends, or when the hub withholds it (`blocked`, a flagged server). The panel
  never counts a time earlier than one it saw before or than the last lease's signed issue time plus the time
  measured since, so turning the clock back does not extend a licence. When the hub reports an integrity violation
  or a suspension, every plugin is switched off.
* A verified 401 `key_revoked` (the owner revoked this server's key on app.sudoo.sh) makes the panel stop using that
  id and key: it makes a new identity, registers it, and must be linked again.
* The heartbeat sends the version, the hash of the panel's Python files, the fingerprint, the installed plugins and
  coarse server figures (CPU, memory and disk use, how many instances exist and run, their Odoo versions and
  edition); nothing about instances' contents, accounts or their data. A purchase also sends the name and e-mail
  address of the super admin who pressed **Buy**, shown on the checkout page and the receipt. Opening a plugin's
  page in the Marketplace tab tells the hub which plugin was viewed (kept only with the account owner's consent).
* Root on the server can read plugin files and the vault key; see [What is not protected](#7-what-is-not-protected).

---

## 12. Checklist for the administrator

- [ ] Create the first account with the setup code right after installing, so nobody else can.
- [ ] Turn on a second factor for your own account; keep `REQUIRE_SECOND_FACTOR` at `admins` or `everyone`.
- [ ] Back up `vault.key` separately from other backups (without it, encrypted settings and keys cannot be read).
- [ ] Give the Super admin and Administrator roles only to people trusted with the server; give everyone else the
      smallest role that is enough (Viewer, Developer, Maintainer).
- [ ] Delete accounts of people who no longer need them; reset a password when in doubt.
- [ ] Look at the audit log from time to time for refused actions and failed sign-ins.
- [ ] Keep real customer data out of instances.
- [ ] Prefer the GitHub App to deploy keys; install it on **selected repositories** where all are not needed.
- [ ] Remove deploy keys of repositories that are no longer used, in the panel and on GitHub.
- [ ] Give each system its own API token, `read` where reading is enough; revoke tokens no longer used.
- [ ] With `host-ports`, put https in front of the panel and limit who can reach its port.
- [ ] Install only the plugins you need, preferably from Official or Verified publishers; read what each asks
      permission to do.
- [ ] Keep `DRIFT_ACTION=restore`, give super admins an e-mail address, and look into every "changed outside the
      panel" message: only root or the `docker` group can cause one.
- [ ] Keep the `docker` group to administrators trusted with root.

---

## 13. Releases and signatures

Servers run prebuilt images; nobody builds or downloads the source code to install. How you can check what you run:

| What | How it is protected |
| :--- | :--- |
| Images `ghcr.io/sudoo-sh/sudoo-panel` and `ghcr.io/sudoo-sh/sudoo-web` | Built by GitHub Actions (`.github/workflows/images.yml`) from the tagged commit, for linux/amd64 and linux/arm64, with an SBOM and a provenance attestation, and signed by digest with Sigstore keyless signing (cosign): the certificate names the workflow and the tag. |
| `compose.yaml` of a release | Names both images by digest, so a tag that moves later cannot change what a server runs. Signed by the release workflow (`compose.yaml.sigstore.json`) and listed in `SHA256SUMS`. |
| `sudoo-install.sh` | Pins the same digests. Checked (shellcheck, tests, a dry run against the release's images) and attached to the release with its SHA-256 in `SHA256SUMS`, and a Sigstore bundle (`sudoo-install.sh.sigstore.json`) when it is signed. When it updates to another version it downloads that release's installer and runs it only if the SHA-256 matches. |

Check an image yourself with [cosign](https://docs.sigstore.dev/cosign/system_config/installation/) 3 or newer (the
digest is in the release's `compose.yaml`):

```bash
cosign verify ghcr.io/sudoo-sh/sudoo-panel@sha256:<digest> \
  --certificate-identity-regexp '^https://github\.com/Sudoo-sh/sudoo\.sh/\.github/workflows/images\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Images of `main` (`edge`) are signed the same way, with `@refs/heads/main` in the identity. When `cosign` is
installed on the server, the installer runs this check itself before it uses an image and stops if it fails.

The SBOM and the provenance are attached to the images: `docker buildx imagetools inspect --format
'{{ json .SBOM }}' ghcr.io/sudoo-sh/sudoo-panel:<version>` (or `.Provenance`).

Check the release's `compose.yaml`:

```bash
cosign verify-blob compose.yaml --bundle compose.yaml.sigstore.json \
  --certificate-identity https://github.com/Sudoo-sh/sudoo.sh/.github/workflows/release.yml@refs/tags/v<version> \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```
