# Security

**Audience:** administrators, and anyone reviewing the panel's security.
**Covers:** what is protected, against whom, how, and what is deliberately not protected.

---

## Contents

1. [What is protected, and from whom](#1-what-is-protected-and-from-whom)
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
14. [Off-site copies](#14-off-site-copies)
15. [Web shell](#15-web-shell)
16. [Mail catcher](#16-mail-catcher)
17. [Support access](#17-support-access)
18. [Servers](#18-servers)
19. [Code profiler](#19-code-profiler)
20. [Production access rules (beta)](#20-production-access-rules-beta)

---

## 1. What is protected, and from whom

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
* With `EXPOSURE=direct` (the default of new installations), only the gateway listens on public addresses: ports 80
  (the ACME authority's checks and a redirect to https) and 443 (https, a certificate for every name), and each
  instance's gateway port, over https with the panel's own certificate. https ends on the server itself; no third
  party sees the traffic. The web service, Odoo and the databases listen on `127.0.0.1` only; the gateway forwards
  the panel's own address to the web service.
* With `EXPOSURE=proxy`, the web service and the gateways listen on `PROXY_LISTEN` (`127.0.0.1` by default) and the
  owner's proxy handles https. They trust the proxy's header with the visitor's address, so nothing but the proxy
  may reach their ports.
* With `EXPOSURE=tunnel`, the web service, every gateway and every Odoo listen on `127.0.0.1` only; nothing is
  reachable from the internet except through the connectors the server opened itself. The tunnel service carries
  the traffic over https and decrypts it at its edge; between the service and the server it travels inside the
  connector's own encrypted connection. See [Public addresses](concepts/PUBLIC_ADDRESSES.md#7-is-it-safe).
* With `host-ports` (an earlier option), the web service and the gateways listen on every interface, on purpose;
  Odoo itself and the databases still listen on `127.0.0.1` only. Use a firewall to limit who can reach the ports,
  and https in front of them on any network you do not fully trust (see [Plugins](PLUGINS.md#host-ports)).
* SSH for administrators, in every mode.
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
| Container | read-only file system, all capabilities dropped except `NET_BIND_SERVICE` (to open ports 80 and 443 for custom domains), `no-new-privileges`, at most 256 processes | as before |

The two talk through Unix sockets in that folder (mode `700`, root only), never over the network: `gateway.sock`
(the backend asks the gateway to reload its routes and collects the request counts) and `panel.sock` (the gateway
asks the backend to start a paused instance, naming it; the backend checks the name and decides, through the same
job as a start from the panel). A process that can open those sockets is root on the server already, so they need
no further secret; the instances' containers have no access to the folder. Everything the gateway reads from
`routes.json` is checked (instance names, ports, a known listen address and header name, only the gateway's own
settings), and everything the backend reads from the gateway (visits, counts, errors) is checked and limited to known
instances. Someone who took over the gateway service could read and change visitors' traffic to the instances (as
any reverse proxy could), but would not reach Docker, the panel's data or its secrets.

### Custom domains and certificates

With `CUSTOM_DOMAINS=acme` the gateway also answers on ports 80 and 443 of every interface, for the custom domains
of the instances ([Administrator Guide](ADMIN_GUIDE.md#19-custom-domains-and-certificates)):

* **Names.** Only full host names are accepted: no IP address, no wildcard, no local or reserved name, nothing under
  `sudoo.sh`, nor the server's or panel's own names. A domain belongs to one instance at most. The gateway answers a
  TLS handshake only for a domain that has a certificate (by SNI; an unknown name ends the handshake), and serves a
  request only when its `Host` is the name the handshake was for (`421` otherwise), so one domain cannot be used to
  reach another instance. Port 80 answers only ACME checks and a redirect to https for known domains.
* **Ownership.** A domain is served only after its DNS points at this server (every address of it is one of the
  target's), checked from the server, and its certificate needs the authority to fetch a random answer through it.
  Whoever controls the domain's DNS controls where it points: removing the record (or the domain in the panel) ends
  it.
* **TLS.** TLS 1.2 or later, Python's default cipher suites; one P-256 key per certificate, made on the server, never
  leaving it.
* **Keys.** Certificate keys are files of root only (mode 600) in `<ODOO_ROOT>/gateway/certs/` (mode 700); they are
  not encrypted, because the gateway service, which holds no vault key, must read them. The ACME account key is
  encrypted with the vault key. Removing a domain deletes its key at once. The gateway service can write in that
  folder, so the panel reads, writes and removes certificates there (also those that go with a moved instance's
  name) without following a link it finds, and reads only regular files of bounded size.
* **Visitors' address.** Visitors of a custom domain connect directly, so no header is trusted for their address: the
  gateway appends the connection's own address to `X-Forwarded-For`, and `X-Forwarded-Proto` is `https`.
* **The ACME client** (`domains/acme.py`) talks only https to the configured authority (http only for `localhost`),
  with certificate checks on; HTTP-01 answers are written only for tokens of the expected form and removed after
  each check.

With `CUSTOM_DOMAINS=external` nothing new listens: the visitor's own proxy handles https and reaches the
instance's gateway port as before.

### The visitor's address

Sign-in lockout, rate limits and the audit log count by the visitor's address, so it must not be something a
visitor can make up:

| Source | Trusted when |
| :--- | :--- |
| `X-Sudoo-Client-IP` | Only with `EXPOSURE=direct` (the exposure plugin's `panel_client_ip_header`): the gateway sets it to the address of the visitor's connection when it forwards the panel's own address, after removing any copy the visitor sent; the web service listens on `127.0.0.1` only, so nobody else can send it. Ignored in every other mode. |
| The exposure's own header | `CF-Connecting-IP` only with `tunnel` (`cloudflare-quick`), where the tunnel service sets it; `PROXY_CLIENT_IP_HEADER` (`X-Real-IP` by default) only with `proxy`, where the owner's proxy must set it on every request (for a list, the last address). The exposure plugin's `client_ip_header`; with `direct` and `host-ports` there is none. |
| `X-Forwarded-For` | Only when the request reaches the backend from `127.0.0.1`, that is from the web service, which replaces any value sent by the visitor with the address it received the request from. |
| The connection's own address | In every other case. |

Odoo, behind the gateway, runs in proxy mode and believes the **last** address in `X-Forwarded-For`. The gateway
appends the address it can vouch for (the exposure's own header with `tunnel` and `proxy`, the connection's own
address with `direct` and `host-ports`) and sets `X-Forwarded-Proto` and `X-Forwarded-Host` itself, so a visitor cannot choose the address
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
  one up after signing in. Removing a factor, replacing one that is set up, or adding one next to a factor already
  set up, needs the password; removing or replacing one ends the account's other sessions. A new sign-in e-mail address (in the profile or through the e-mail code) ends them too.
* **Recovery codes** (`RECOVERY_CODES_COUNT`, 10 by default) come with the authenticator app and are shown once.
  Each is 10 characters from an alphabet of 31 (about 49 bits), works once in place of the app's code, and is
  stored only as HMAC-SHA256 keyed with a key derived from the vault key and bound to the user name. Spending one
  is atomic (two requests with the same code: one succeeds). Wrong codes count like wrong passwords, every use is
  audited, new codes need the password and replace the old ones, and removing the app removes them.
* A lost phone without recovery codes: an administrator removes the account's factors (**Accounts**, or
  `DELETE /api/v1/users/<name>/second-factors`), which removes the recovery codes and signs it out everywhere.
* **Sign-in e-mail** (`SIGN_IN_EMAIL`, on by default): every successful sign-in is e-mailed to the account with the
  time, address, browser and how it signed in, in the background so a mail failure never blocks a sign-in.
  `SIGN_IN_EMAIL_OPT_OUT` decides whether people may turn it off. **Sign out others** on **My account**
  (`DELETE /api/v1/account/sessions`) ends every other session of the account.
* More factors (SMS, hardware keys) can be added as plugins ([Plugins](PLUGINS.md#8-more-plugin-types)).

### Sign-in with GitHub and e-mail codes

Besides the password, an account may sign in with a code sent by e-mail or with GitHub, as an administrator chooses
for it (`SIGN_IN_METHODS`, [Admin guide](ADMIN_GUIDE.md#ways-to-sign-in)). Each is a first factor:
`REQUIRE_SECOND_FACTOR` applies after it as after a password.

**Threat model of GitHub sign-in:**

| Threat | What stops it |
| :--- | :--- |
| A forged or replayed return from GitHub (login CSRF: signing someone into the attacker's account) | A random `state` (256 bits) bound to the browser's session cookie (`SameSite=Strict`, `HttpOnly`) and used once; the flow expires after 10 minutes. A return the browser did not start is refused. |
| A code intercepted on its way back (a log, a referrer, a malicious extension) | PKCE (S256): exchanging the code also needs a 512-bit verifier that never leaves the server's memory, plus the app's client secret. |
| A stolen user token | The token is used for the few calls sign-in needs and revoked at once (`DELETE /applications/<client id>/token`); it is never written anywhere. Repository permissions are read later with the app's own installation token. The app's client secret is stored encrypted with the vault key. |
| Taking over an account by e-mail address | An account is linked by e-mail address only when it is the person's **verified primary** address on GitHub. Otherwise only a GitHub user name an administrator typed links an account, at the first sign-in. |
| A renamed or re-registered GitHub login | Once linked, the account is found by GitHub's numeric user id, which never changes; the stored login is only shown. A login an administrator typed is used once, for the first link: type it shortly before the person signs in. |
| Strangers | Only linked accounts, accounts or invitations that name the person, and members of the organisations listed in `GITHUB_SIGN_IN_ORGS` get in; everyone else is refused and audited. |
| GitHub unreachable, or the app deleted | Break glass: at least one super admin always keeps a password, and the panel refuses changes that would take the last one away. |
| Losing access on GitHub | With `GITHUB_ACCESS_FROM_REPOSITORIES`, repository permissions are read again at each sign-in and every `GITHUB_ACCESS_CHECK_MINUTES`; a permission not read for three intervals (one hour at least) counts as none. |

`GITHUB_SIGN_IN_SKIPS_SECOND_FACTOR` (off by default) trusts GitHub's own two-factor authentication instead of the
panel's, and only when GitHub reports it on for the account. The trade-off: one less code, but anyone with a
signed-in GitHub session on a device gets into the panel too, and GitHub's account recovery becomes the panel's.

**E-mail codes** (no password) are 6 digits, valid 10 minutes, used once, kept only as a hash, with 5 tries; a new
one at most once a minute per account and `EMAIL_CODE_SENDS_PER_HOUR` per address. The page answers the same, and
the e-mail is sent in the background, whether or not an account uses the address, so it does not tell who has an
account. Wrong codes count like wrong passwords. After an e-mail code, an e-mail code is not accepted as the second
factor: the same mailbox would prove nothing more. Whoever controls the mailbox controls such an account, so keep
this way for people whose mailbox is protected well (it is the default for client accounts only).

---

## 4. Stored secrets: the vault

Some secrets must be **read back** by the panel, so they cannot be hashed. They are **encrypted**:

| Secret | Stored as | Read when |
| :--- | :--- | :--- |
| GitHub deploy keys (`keys/deploy/*.key`) | AES-256-GCM, label `deploy-key:<owner>/<repo>` | For the length of one git connection, decrypted into a memory-only file readable by root, then deleted |
| The GitHub App's private key, webhook secret and client secret (`keys/github-app.json`, mode 600) | AES-256-GCM, labels `github-app:pem`, `github-app:webhook_secret`, `github-app:client_secret` | The private key to sign a short JWT when a new installation token is needed; the webhook secret to check each delivery; the client secret is kept but not used |
| GitHub App installation tokens (read-only, one hour) | AES-256-GCM in a file in `MEMORY_DIR` (memory only, mode 600), label `github-app-token:<installation>` | When git needs one (through the panel's credential helper) and for API calls; reused until five minutes before they expire |
| Odoo master password of each instance (`MASTER_PASSWORD` in its `.env`) | AES-256-GCM, label `master-password:<name>` | When someone with the permission `instances.credentials` opens an instance's **Access** tab (and the audit log records it) |
| Secret settings changed in the panel (`smtp_password`, `odoo_admin_password`, `github_webhook_secret`, the alert webhooks' `alert_webhook_<n>_url` and `alert_webhook_<n>_secret`) | AES-256-GCM in `/opt/odoo/manager/settings.json`, label `setting:<key>` | When the panel starts, and when a setting is changed |
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
picked by "Restore the latest backup", and are neutralised when they are put back. The manifest also records the
instance's project and branch, so their access keeps applying to its backups once it is deleted (listing,
download, deletion, restore); a deleted instance's backup taken by an earlier version, which does not record them,
is only for those from whom no project is hidden.
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

* The secret settings (`SMTP_PASSWORD`, `ODOO_ADMIN_PASSWORD`, `GITHUB_WEBHOOK_SECRET`, the alert webhooks' addresses
  and secrets) are **write-only**: the page and the API only say whether they are set, never their value. In
  `settings.json` they are encrypted with the vault key; without `vault.key` they cannot be read back.
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
| **Traffic at the tunnel service** | `EXPOSURE=tunnel` ends https at the tunnel service's edge. For instances without real data this is acceptable; for https that ends on the server use `direct` (the default of new installations) or your own proxy ([Public addresses](concepts/PUBLIC_ADDRESSES.md#7-is-it-safe)). |
| **The panel's own certificate** | With `EXPOSURE=direct` and no name for the panel, the panel (and every instance without a name, on its gateway port) is served with a certificate the panel makes itself; browsers warn, and a visitor who accepts the warning cannot tell this server from another. Set a name (`PANEL_DOMAIN`) before people sign in from untrusted networks. |
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
| `instances.logs` | Read Odoo's log | yes | yes | yes | yes | yes |
| `instances.update` | Get new code, update submodules, restart, start, add submodules | yes | yes | yes | yes | |
| `instances.operate` | Pause, switch off, upgrade modules, switch branch, back up, remove submodules, change automation, make the lifetime longer | yes | yes | yes | | |
| `instances.create` | Create instances | yes | yes | yes | | |
| `projects.manage` | Create and link projects (one GitHub repository each), change their settings and the stages of their branches | yes | yes | yes | | |
| `projects.access` | Choose who may see and work in a project and its branches (grants, restricted projects and branches); lock and unlock branches | yes | yes | | | |
| `teams.manage` | Create, change and delete teams | yes | yes | | | |
| `instances.restore` | Restore an instance from a backup (replaces its database and files) | yes | yes | yes | | |
| `instances.upgrade` | Upgrade an instance's database to a newer Odoo version: the pre-check, the test on a new staging instance, upgrading production (with `"confirm_production": true`) and cancelling it | yes | yes | | | |
| `instances.delete` | Delete instances, also by giving one a shorter lifetime | yes | yes | | | |
| `instances.credentials` | See Odoo passwords and ports | yes | yes | yes | | |
| `instances.resources` | Set an instance's resources: Odoo workers, time and memory limits, container limits | yes | yes | | | |
| `caught_mail.view` | Read the e-mails caught from staging and development instances (the Mail tab) | yes | yes | yes | yes | |
| `caught_mail.manage` | Delete caught e-mails and release them (send a copy to an address) | yes | yes | | | |
| `backups.manage` | Download and delete backups; see, check and copy again off-site copies | yes | yes | | | |
| `offsite.configure` | Set up where off-site copies go (the `OFFSITE_*` settings), and test it | yes | | | | |
| `offsite.restore` | Restore an instance from an off-site copy (with `instances.restore`) | yes | yes | | | |
| `offsite.delete` | Delete off-site copies | yes | yes | | | |
| `domains.manage` | Add and remove instances' custom domains, choose the primary one (seeing them needs `instances.view`) | yes | yes | | | |
| `github.manage` | Manage deploy keys; see the GitHub App's state | yes | yes | yes | | |
| `accounts.manage` | Create, change and delete accounts | yes | yes | | | |
| `tokens.manage` | Create and revoke API tokens | yes | yes | | | |
| `audit.view` | Read the audit log | yes | yes | | | |
| `plugins.view` | See installed plugins, the marketplace and purchases | yes | yes | | | |
| `settings.manage` | Change the panel's settings, install and buy plugins, and restart it | yes | | | | |
| `mail.manage` | Change the texts of the e-mails the panel sends (E-mail templates) | yes | | | | |
| `instances.shell` | Open a terminal in instances' Odoo containers, and `odoo shell` ([Web shell](#15-web-shell)) | yes | yes | `SHELL_ROLES` | `SHELL_ROLES` | |
| `instances.database_shell` | Open `psql` in instances' database containers | yes | yes | | | |
| `shells.supervise` | See open and past shells, end them, play their recordings | yes | | | | |
| `instances.profile` | Record what an instance's Odoo processes spend their time on (code profiler), read, download and delete the recordings ([Admin guide](ADMIN_GUIDE.md#28-code-profiler)) | yes | yes | `PROFILER_ROLES` (default) | `PROFILER_ROLES` | |

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
* A token **belongs to the account that created it** (its owner): its permissions are its scope's role ∩ the owner's
  current role, and the owner's teams, project access and locks apply to it (below). A token whose owner was deleted
  keeps its scope's role but has no grants and no teams: restricted projects and locked branches are closed to it.
  A token created by a token belongs to the same account, and a token whose owner was deleted cannot create one.
* Roles without `instances.credentials` never receive passwords, master passwords or ports, even in API answers.
* Accounts of earlier versions were renamed once, when the panel started: `admin` became Super admin, the sync
  operator became Developer.
* The audit log (in `/opt/odoo/manager/sudoo.db`, shown under **Audit log**) records sign-ins, failed sign-ins,
  lockouts, wrong setup codes, refused actions, every action on an instance, viewed credentials, settings changes,
  and account and key changes, with time, account, address and result. Passwords are never written to it.

### Teams, project access and locked branches

Inside a project, every check goes through one function (`projects/access.py`): the permission must be in the
caller's **role** and in the **level** the project gives them (role ∩ grant), and the branch must not be **locked**
against them. Permissions about the whole panel (accounts, tokens, settings, the audit log, plugins, GitHub, off-site
storage, teams) depend on the role alone.

| Level | Project permissions it can give (never beyond the role) |
| :--- | :--- |
| none | nothing; the project or branch is hidden (404, left out of every list and live event) |
| view | `instances.view`, `instances.logs` |
| developer | view + `instances.update`, `caught_mail.view`, `instances.shell` |
| maintainer | developer + `instances.create`, `instances.operate`, `instances.restore`, `instances.credentials`, `projects.manage` |
| admin | every project permission, `projects.access` included |

* **Which level applies.** The branch's grants first, then the project's; among them the person's own grant (also a
  `none`), else the highest level of their teams; with no grant, the role decides in a project open to everyone
  (`panel_roles`, the mode of every project made before teams existed, so an upgrade locks nobody out) and nothing in
  a `restricted` project or branch. An access mode the panel does not know gives nothing.
* **Super admins** have the level admin everywhere: grants never hide a project from them.
* **Locked branches.** A lock takes every project permission except `instances.view` from whoever it does not allow,
  super admins included: credentials, logs, caught e-mails, shells, actions, settings, stages, new instances, backups
  and restores (local and off-site, also a backup used for another instance), deletion, and copying a locked
  production into staging. The checks are made in the API, when a shell ticket is issued and again on the shell's
  WebSocket (and every few seconds while it is open), by the gateway (a visit never starts a paused instance of a
  locked branch), and before automatic work (pushes, expiry, deleted branches, new branches). Locking needs
  `projects.access` in the branch; changing or removing a lock also needs to be allowed by it, except for super
  admins, who can always unlock (audited). Daily backups, monitoring and certificate renewals continue, and a
  locked production branch keeps serving.
* **Lists and live events** leave out the projects, branches, instances, jobs, backups and off-site copies the caller
  may not see, and the live stream filters each event per caller (metrics included); a project the caller lost
  access to is sent as deleted. Job events whose instance the panel cannot tell go only to callers from whom
  nothing is hidden.
* **Jobs' technical logs stay on the server.** No endpoint, live event, download, alert or e-mail sends a job's
  technical log (the commands it runs and what they print), whatever the caller's role: it goes to the backend's own
  log (`docker logs sudoo-panel`), each line prefixed with the job and the instance, with addresses' passwords and
  masked secrets replaced. People see the steps, the progress, the notes, the result and "Why it failed", whose error
  details are only the lines that explain the failure, with server paths, IP addresses, container names, tokens and
  credentials in addresses removed (`core/failure.py`). Odoo's own log (the **Logs** tab) is still shown to roles with
  `instances.logs`.
* **Branch rules.** Adding, changing or removing a branch's own rule (its grants, whether it is restricted) needs
  `projects.access` in that branch, so an administrator a restricted branch leaves out cannot lift the restriction or
  grant themselves into it.
* **New instances in a branch** need `instances.create` there, however they come: created, connected to the
  project's repository from plain Odoo, or made as a database upgrade test. Running an upgrade test again on its
  existing test instance needs `instances.restore` on that instance, so its branch's lock applies.
* **Automatic copies** of a restricted production into staging (a push) are made only when nobody could open the copy
  who cannot open production: accounts, API tokens (also those whose owner is gone) and active Sudoo.sh support
  grants are all counted.
* **No lockout.** A change of a project's access that would leave the person making it without `projects.access`
  there is refused.
* **Audit.** Team changes (with members added and removed), access changes, locks, changed locks, unlocks, skipped
  automatic updates of locked branches and every refusal are in the audit log.
* The public addresses of instances stay reachable for visitors: access and locks govern the panel's ways in (and
  waking a paused instance), while Odoo's own sign-in protects the database. A locked or hidden branch's running
  instance still answers at its link.

### The plan's entitlements

What the plan allows is read only from the **signed lease** (its `entitlements` claim, verified with the hub's keys
like every lease; the plain JSON next to it is ignored), kept with its `valid_until` and compared with the panel's
trusted clock (`trusted_now`: turning the clock back does not extend it). A panel that is not linked, never heard
from the hub, or has `MARKETPLACE` off has the Free plan; a lease that cannot be verified changes nothing. When the
entitlements expire, only new production branches are refused: nothing that runs is ever stopped or deleted, and a
`production_keep` after a downgrade only changes stages. Self-hosted code can be patched; the hub compares what each
server reports (`usage`: servers, projects, production branches and their names, instances by stage) with the plan and flags mismatches.

---

## 10. Instance containers

Each instance is two containers, `odoo` and `db` (PostgreSQL), plus a tunnel connector with `EXPOSURE=tunnel`. They exist to run Odoo for visitors who come through the panel's gateway, and nothing
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
| Published ports | `127.0.0.1:<ODOO_PORT>` only, and `127.0.0.1:<ODOO_GEVENT_PORT>` (WebSockets) when it runs with Odoo workers | none | none |
| Limits | processes (`CONTAINER_PIDS_LIMIT`); the stage's priority weights (`cpu_shares`, `mem_reservation`, `oom_score_adj`, never `-1000`); memory and processors only when an admin sets them (`<STAGE>_ODOO_MEMORY_LIMIT`, `<STAGE>_ODOO_CPU_LIMIT` or the instance's own) | the same, with `<STAGE>_DB_MEMORY_LIMIT`, `<STAGE>_DB_CPU_LIMIT` | 256 processes, 256 MB |
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
break or another control character (C1 controls and the Unicode line and paragraph separators included), and it
reads lines split at line feeds only. Values it reads from an instance, such as the login of the Odoo administrator
after a restore, are only recorded when they are a single short line.

The instance folder is mode 700 and owned by root; `.env` and `docker-compose.yml` are mode 600; `config/` and
`odoo.conf` are mode 700 and 600 and owned by the Odoo image's user, so Odoo (which gets the folder read-only) can
read its configuration and no other user on the server can.

**Only through the panel.** Nothing of an instance listens anywhere but `127.0.0.1`; visitors reach Odoo through the
panel's gateway (the only part that listens on public addresses with `EXPOSURE=direct`), and people change instances through the panel's jobs, each checked against a permission and written
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
| Hub keys | Ed25519 public keys | The panel's database, starting from the root keys (`HUB_PUBLIC_KEYS`, else the keys the release ships for hub.sudoo.sh; for another hub, the keys it lists the first time) | Verify every answer, lease, command and package. A key the hub lists as `active` is accepted, a `retiring` one until its `not_after`; a `revoked` or `expired` one is dropped at once and for good. A list only moves a known key forward (active, retiring, expired, revoked): it never makes a retiring key active again or its `not_after` later. A new key is trusted only when its endorsement, signed by a trusted key usable now (active, or retiring before its `not_after`), verifies (`sudoo-hub-key-v1`, key id, public key, `not_before`). Premium release and binding keys follow the same rules |
| Answer signature | Ed25519, header `Sudoo-Signature: kid=, ts=, sig=` over `sudoo-hub-response-v1`, the request's method, path, installation id and nonce, the status, the time and the body's SHA-256 | Checked on every answer, refusals and downloads included (a download over its `X-Sudoo-Package-SHA256`) | An answer without a valid signature from an accepted key, or more than 5 minutes from the server's clock, is not believed: it counts as "the marketplace cannot be reached". A changed or replayed answer (another request's nonce) fails |
| Lease | JWT signed by the hub (EdDSA only; `none` and HMAC algorithms are refused), for this installation, 72 hours | The panel's database | Says which plugins may run, the licence dates of the paid ones (with `run_until`) and the flags; only verified claims are used |
| Commands | Ed25519 over `sudoo-hub-command-v1`, id, installation id, kind, time and the SHA-256 of the canonical JSON payload | Carried out once, recorded in the panel's database | `remove_paid_plugins` (after the server is logged out of its account): back up, remove the listed paid plugins, acknowledge. A command whose signature does not verify is ignored |
| Hardware fingerprint | SHA-256 of `sudoo-fingerprint-v1`, the installation public key, the host's `/etc/machine-id` (mounted read-only) and the primary MAC address | Sent at registration and in every heartbeat; the hub keeps a keyed digest | A copy of the panel's data on another machine is flagged: its paid licences are withheld until the account owner accepts the machine or revokes the key |
| Package signature | Ed25519 over `sudoo-plugin-v1`, id, version and SHA-256 | Sent with the package | Binds a package to one plugin and one version; checked with an accepted hub key before anything is unpacked |
| Plugin file hashes | SHA-256 over every unpacked file | The plugin's record | Checked at every start and every 10 minutes; a changed plugin is not loaded and the super admins are e-mailed |

* Every request to the hub is signed, so every answer is signed back; downloads use a signed request, and a redirect
  to a storage link (itself signed) is followed only over https, without the signature.
* Packages with links, special files, absolute or `..` paths, compiled code, archive bombs or Python dependencies are
  refused, and so is one with a module name the panel already has (`sudoo`, `sudoo_premium*`, or any module it can
  import), counted in each folder the plugin puts on the module path (its root and `src/`): `.py` files and folders,
  with or without `__init__.py`. Premium packages go on the module path before plugins; plugins are unpacked into `/opt/odoo/manager/plugins/` (folders 700, files 600) and the package file is
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
      Save the recovery codes of the authenticator app somewhere away from the phone.
- [ ] Set up e-mail and keep `SIGN_IN_EMAIL` on, so people hear about sign-ins that were not theirs.
- [ ] Back up `vault.key` separately from other backups (without it, encrypted settings and keys cannot be read).
- [ ] Give the Super admin and Administrator roles only to people trusted with the server; give everyone else the
      smallest role that is enough (Viewer, Developer, Maintainer).
- [ ] Delete accounts of people who no longer need them; reset a password when in doubt.
- [ ] Look at the audit log from time to time for refused actions and failed sign-ins.
- [ ] Keep real customer data out of instances.
- [ ] Prefer the GitHub App to deploy keys; install it on **selected repositories** where all are not needed.
- [ ] Remove deploy keys of repositories that are no longer used, in the panel and on GitHub.
- [ ] Give each system its own API token, `read` where reading is enough; revoke tokens no longer used.
- [ ] With `EXPOSURE=direct`, give the panel a name (`PANEL_DOMAIN`) so it has a trusted certificate; open only ports
      80 and 443 (and the gateway ports only if instances without a name must be reachable).
- [ ] With `EXPOSURE=proxy`, keep `PROXY_LISTEN=127.0.0.1` (or firewall the ports) and have the proxy replace the
      visitor-address header on every request.
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
| `sudoo-install.sh` | Pins the same digests. Built by the release workflow (`.github/workflows/release.yml`) from a pinned commit of the installer's repository, after its shellcheck, tests and a dry run against the release's images, and signed there with Sigstore keyless signing (`sudoo-install.sh.sigstore.json`); no maintainer login signs it. Listed in `SHA256SUMS`. When it updates to another version it downloads that release's installer, checks the SHA-256 and, when `cosign` is installed, the signature for exactly that release's workflow and tag; a missing or failing signature stops the update. Without `cosign` only the SHA-256 is checked, and it says so. Installers of releases before 3.0.0 are not signed by the workflow. |

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

Check the release's installer before running it (both files from the release page; the identity names the tag):

```bash
cosign verify-blob sudoo-install.sh --bundle sudoo-install.sh.sigstore.json \
  --certificate-identity https://github.com/Sudoo-sh/sudoo.sh/.github/workflows/release.yml@refs/tags/v<version> \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

A signature shows who built the file: the release workflow of this repository, run for that tag. `SHA256SUMS` alone
shows only that the file is the one the release lists.

---

## 14. Off-site copies

Backups copied off the server ([Admin guide, Off-site copies](ADMIN_GUIDE.md#20-off-site-copies)) are signed and
checked:

* Each copy has a `manifest.json` listing its files with their size and SHA-256, and `manifest.json.sig`, an Ed25519
  signature of the manifest's exact bytes. The fingerprint of a key is `SHA256:` and the SHA-256 of the public key in
  DER, as `openssl pkey -pubin -outform DER | sha256sum` prints it.
* The private key is made the first time off-site copies are switched on and stored encrypted with the vault key
  (AES-256-GCM, label `offsite-signing-key`) in `<ODOO_ROOT>/manager/offsite/signing.key`, mode 600. The access
  key's secret is a setting stored the same way, never shown again.
* Before a copy is used (restore) or when it is checked (**Verify now**, or every `OFFSITE_VERIFY_DAYS`), it is
  downloaded into `<ODOO_ROOT>/backups/.offsite/` (mode 700) and checked: the signature, that the manifest names this
  very copy (instance, time and kind, so a signed copy cannot be moved and passed off as another), and every file's
  size and checksum. The check **fails closed**: a missing manifest or signature, another key, a changed file or a
  file not listed stops it, and nothing is restored. The failure is in the audit log and alerted.
* Every request to the storage is signed (AWS Signature Version 4), over https (http only for `localhost`), and
  carries the SHA-256 of its body.

**The trust model.** The signature protects against someone who can **write to the bucket** but not to the server:
they cannot change, replace or move a copy unnoticed. It does **not** protect against **root on the server**, who
can read the vault key and sign anything, nor against deletion by someone holding the access key (use the provider's
versioning or object lock). Copies are not encrypted by the panel. Keep a copy of the **public key off the server**:
after the server is lost, checking the copies with it (pasted in the restore dialog, or with `openssl` by hand) is
what proves they were made by your panel.

Configuring the storage needs `offsite.configure` (super admins only); restoring from a copy needs `offsite.restore`
and `instances.restore`; deleting a copy needs `offsite.delete`. Each is audit-logged, as is every test of the
storage, every check someone asks for, and every copy removed by the retention rules.

---

## 15. Web shell

The Shell tab gives a person a terminal inside an instance's containers ([Administrator guide](ADMIN_GUIDE.md#21-web-shell)).
That is as much power over the instance as the panel itself has, so it is guarded more than any other feature.

### Threat model

| Threat | What stops it |
| :--- | :--- |
| Someone without the right role opens a shell, or a role loses it while a shell is open. | Permissions `instances.shell` and `instances.database_shell`, checked when the ticket is asked for, again when the socket opens, and every 15 seconds while it is open (with `SHELL_ENABLED`, `SHELL_PRODUCTION`, `SHELL_ROOT`); a failed check closes the shell. Viewers never have them; `SHELL_ROLES` adds maintainers or developers to the Odoo container only. |
| Another website opens a WebSocket with the visitor's cookie (cross-site WebSocket hijacking): browsers apply neither CORS nor the same-origin policy to WebSockets, and cookies go along. | The socket needs a **ticket**, which only an ordinary authenticated change can obtain: `POST .../shell/tickets` with the session cookie (SameSite=Strict), the panel's Origin and the `X-CSRF-Token` header, which another site cannot read or send. The socket also refuses any upgrade whose `Origin` is missing or not the panel's own address. |
| A ticket leaks (logs, history, a shared screen) and is replayed. | A ticket is 32 random bytes, valid for `SHELL_TICKET_SECONDS` (15), used up when presented (even if refused), and accepted only with the cookie of the **same panel session** (same person, same session id) and for the same instance. Only its SHA-256 is kept, in memory. It travels as a WebSocket subprotocol, never in the address, so it is in no access log. |
| A stolen or signed-out session keeps a shell open. | Signing out, a password change or a deleted account ends the panel session; the shell checks every 15 seconds and closes. The shell's own limits (idle 15 minutes, longest 60) apply whatever the session's. |
| An API token opens a shell. | Refused for every scope: a shell is for a person in the panel. |
| A shell reaches the server or the panel, or another instance's containers. | Commands run only through `docker exec` in a container whose name is exactly `odoo-<instance>-odoo-1` or `odoo-<instance>-db-1` and that carries the panel's labels for that instance and service; the instance name comes from the path and is checked against the allow-list. The panel's own container and the server are never targets. A container changed to be privileged, share the server's processes, network or IPC, or mount the Docker socket is refused. |
| A shell escalates inside the container. | The containers are hardened ([Instance containers](#10-instance-containers)): every capability dropped (the database keeps a few for PostgreSQL), `no-new-privileges`, read-only system files, process and memory limits. Shells run as the container's own user (`odoo`, `postgres` for psql). Root is only for super admins and only when `SHELL_ROOT` is on; even then the capabilities stay dropped. |
| A shell exhausts the server. | At most `SHELL_MAX_PER_USER` shells per person, `SHELL_MAX_PER_INSTANCE` per instance, 100 on the panel, 5 waiting tickets per person, and the API's rate limit on tickets. The programs run inside the container's memory, processor and process limits, at a lower priority (`nice 10`). Messages from the browser are at most 64 KB. |
| Programs keep running after the shell (a backdoor started with `nohup`, a forgotten loop). | Docker cannot stop an `exec`'d process. Every process of a shell carries `SUDOO_SHELL=<id>` in its environment; when the shell closes, for any reason, the panel runs a short script in the same container, as the same user, that sends SIGHUP and then SIGKILL to every process with that mark. Shells open when the panel stopped are ended when it starts again. A program that deliberately clears its environment can escape this; it still lives only inside that container, within its limits, until the container restarts. |
| Someone denies what they did. | `shell opened`, `shell closed` (duration, reason) and `shell refused` in the audit log, always. With `SHELL_RECORDING` (production instances and root shells by default) what was shown and typed is recorded, encrypted with the vault key, viewable by super admins only (each viewing logged), kept `SHELL_RECORDING_DAYS` and capped in size; when a recording is full only typing is recorded, and the shell closes when that is full too. |
| A recording leaks a secret typed into a program. | Recordings are encrypted per line (AES-256-GCM, vault key, label per shell), mode 600, never sent anywhere, readable only through the API by super admins. Do not type passwords into recorded shells when you can avoid it. |
| Inline styles or scripts injected through the terminal page. | The Content-Security-Policy stays as strict as everywhere (`style-src 'self'`, `script-src 'self'`): xterm.js is part of the panel (no CDN); the few `<style>` elements it writes are ignored by the browser and their text is applied through constructed stylesheets instead. |

### What a shell does not protect against

- A person allowed to open a shell in the Odoo container controls that instance: Odoo's configuration there holds
  the database password, so they can also reach its database (`psql` from that container), read and change all its
  data and files. The separate `instances.database_shell` permission keeps `psql` out of the menu for roles that do
  not need it; it is not a wall between the two containers. Give shell access only to people you would give the
  database to.
- Commands typed in a shell take effect at once; there is no undo. Back up first (or rely on the daily backup).
- A program started in a shell can use the instance's resources up to its container's limits and so slow or restart
  that instance (never the others or the panel).

## 16. Mail catcher

Staging and development instances send their e-mail to the mail catcher, `sudoo-mail`, never to real addresses
([Admin guide](ADMIN_GUIDE.md#23-mail-catcher)).

| Threat | Protection |
| :--- | :--- |
| The catcher's container plants a link or a FIFO in the shared folder | The panel reads `catcher.json` only as a regular file of bounded size, without following a link and without waiting on a FIFO; anything else is replaced when the settings are written (atomically, never through a link). |
| A test instance e-mails real customers | Odoo's `odoo.conf` points at the catcher, and every outgoing mail server in the instance's databases is switched off when it starts, when it leaves production and every 5 minutes. Code that opens its own SMTP connection is not covered (the instances can reach the Internet). |
| One instance reads or fills another's mailbox | The catcher accepts e-mail only after SMTP authentication and files it under the instance whose password was given (a random password per instance; the catcher keeps only its SHA-256). The panel serves each instance's Mail tab from that instance's mailbox only, and checks the e-mail id's form so no path can be given. |
| The catcher as a way into the server or between instances | Its container has no Docker socket, no `.env`, no published port, read-only system files, no capabilities, a process and memory limit, and an internal network of its own. It joins only the Odoo-side network of staging and development instances (never the database network) and forwards nothing. It speaks plain SMTP only on those networks. Lines are limited to 64 KB, e-mails to `MAIL_CATCHER_MAX_MESSAGE_MB`, connections to 64, wrong passwords to 3 per connection. |
| An e-mail's HTML attacks the person reading it | It is never put into the panel's page: it is served on its own address with `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; img-src data:; ...; sandbox` (no scripts, no forms, no remote images or fonts, so no tracking pixel tells the sender it was opened) and shown in an `<iframe sandbox>` of the panel only (`frame-ancestors 'self'`). Inline images (`cid:`) are turned into `data:` addresses on the server. Attachments and the `.eml` always download as `application/octet-stream`. |
| Caught e-mail leaves the server | Only by **Release**: administrators (`caught_mail.manage`), one typed address at a time, through the panel's own mailer, audit-logged with the address; `MAIL_CATCHER_RELEASE=false` switches it off. |
| Caught e-mail piles up | Kept `MAIL_CATCHER_RETENTION_DAYS` (7), at most `MAIL_CATCHER_MAX_MESSAGES` (500) and `MAIL_CATCHER_MAX_MB` (200) per instance; a mailbox is deleted with its instance or when the instance moves to production. Files are mode 600 in a folder only root can open. |

## 17. Support access

Letting Sudoo.sh support into the panel for a while ([Admin guide](ADMIN_GUIDE.md#26-support-access)).

| Threat | Protection |
| :--- | :--- |
| Support reaches the server without being asked | Nothing listens for it: no port, no SSH key, no shared secret. Support's requests come only as answers to the panel's own outbound calls to the hub, and only while a grant made by a super admin or administrator is active; without one the panel does not ask. |
| Someone pretends to be the hub | Every answer of the support channel must carry the hub's Ed25519 signature over the request, its nonce and the body (the same checks as the heartbeat); anything else is dropped. Each request must also name an active grant of this panel and a session the hub says is open on it. |
| Support does more than granted | Requests run as a synthetic user whose permissions are the grant's level minus accounts, tokens, teams, access and locks, GitHub, settings, plugins and support access itself (those paths are refused before the app sees them); the scope limits projects, branches and instances (instances outside every project, and new projects or instances outside one, only with a grant for everything or one that names the instance); locks never let it through. The hub checks the level too (view: reads only; shells only with the shell level). |
| A grant outlives its purpose | It ends at its time (at most 3 days, `SUPPORT_ACCESS_MAX_HOURS`), on **Withdraw** or **End now**, or when `SUPPORT_ACCESS` goes off; the panel checks the grant on every request and every permission check, so the end is immediate on the panel and reaches the hub with the next call. Support shells close at once. Nothing support does can leave a way back in: it cannot create accounts, tokens, invitations, deploy keys or grants. |
| A Sudoo.sh staff member's account is stolen | Each session needs a fresh code from that person's authenticator app (never a password or an e-mailed code), belongs to them alone, lasts at most an hour by default and never past the grant; staff need the `support_access.use` permission. |
| Nobody knows what support did | Every change, refusal, session and shell is in the panel's activity log under "Sudoo.sh support (name)", marked as support; support shells are always recorded (encrypted, played by super admins); the hub keeps its own audit log of sessions, changes and shells, with the staff member's role and reason. |

---

## 18. Servers

With several servers (a premium feature), the main panel manages other servers through their agents
([Admin guide](ADMIN_GUIDE.md#27-servers)).

| Threat | Protection |
| :--- | :--- |
| Someone joins a server of their own to the panel | Joining needs a join code that an administrator with `servers.manage` created: 32 random bytes (the panel keeps only their SHA-256), single use, valid `SERVERS_JOIN_CODE_MINUTES` (30). A joined server can do nothing until an administrator approves it by typing the fingerprint the server printed (two-sided approval). |
| A stolen join code is used before the real server | The thief's key has another fingerprint than the one the real server prints: the approval fails, and the admin revokes the server and adds it again. |
| A fake panel takes over a server | The join code carries the SHA-256 of the panel's public key; the agent pins the key only when the join answer matches it, and from then on accepts only a welcome and frames signed with that key. |
| A server runs code that is not Sudoo.sh's | The agent runs only premium packages signed with the premium key and bound to the panel it joined (the panel's installation and hardware fingerprint, as that panel states over its signed channel), checked when installed and every time it starts, against the server's clock but never earlier than the latest time the panel signed. |
| Messages are read, changed, replayed or reordered on the way | The channel runs over the panel's https address. On top of TLS, every frame is Ed25519-signed by its sender over the session (both sides' random nonces), its direction and its sequence number; the receiver accepts only the next number with a valid signature from the pinned key, else it closes the channel. |
| An agent asks the panel for more than its instances need | The panel never sends its secrets (session key, vault key, GitHub App key, SMTP, off-site and hub credentials, deploy keys). It sends its non-secret instance settings and decisions, and, when a job there asks, a short-lived GitHub App installation token only for an account whose repositories are used by the instances the panel itself placed or moved on that server (what a server reports does not count). |
| A backup of another project is used through another server | Before passing on a request that names a backup (delete, download, restore into an instance), the panel checks the caller's access to backups in the project and branch of the instance the backup was taken from, as on one server; the agent refuses a backup of a project's instance the panel did not check. |
| A server lies about an instance it holds | A moved instance keeps the identity the panel recorded (`INSTANCE_ID`, project), never what a backup's manifest or the server reports; a server that reports another project than the panel recorded gets no move. |
| A request is run on a server with more rights than the person has | The panel computes the caller's permissions in that instance (role, project and branch access, locked branch) and sends exactly that list; the agent runs the endpoint with it and every check applies there. The agent listens on no port: it runs only requests that came over the signed channel. |
| A managed name is pointed somewhere else | Only the main panel asks Sudoo.sh for managed names, with its signed requests; agents never talk to the hub and no server holds a DNS token. A name of another server's instance points at the public addresses that server's agent reported over its signed channel. When an instance moves, the certificate (and key) of its managed name goes over that channel only to the server that serves the name from then on, and the old server deletes its copy. |
| A compromised server attacks the others | Each agent has its own key and sees only its own instances; revoking it closes its channel at once and refuses its key. |
| A compromised server speaks for other instances | What an agent reports, its live events, jobs and log entries are taken only for instances the panel places on that server: never about the main server's instances or another server's. The project of another server's instance is the one the panel placed it in, never the one its agent reports. A move takes the repository and custom domains from the panel's own records, and writes on the main server only a backup named after the moved instance, inside the backups folder. Each agent's report and log entries are limited (`SERVERS_INSTANCES_KEPT`, `SERVERS_REPORT_KB`, `SERVERS_LOG_ENTRIES_PER_DAY`). |
| An agent's answer runs as the panel | An answer passed on from an agent keeps only a JSON or plain-text type (a caught e-mail's HTML keeps its sandboxed frame policy); anything else is sent as a download. Every such answer gets `nosniff` and a sandboxed `Content-Security-Policy` with no script, and only redirects to the panel's own paths. |

---

## 19. Code profiler

The code profiler ([Admin guide](ADMIN_GUIDE.md#28-code-profiler)) is the only place the panel starts a container
with a capability an instance's containers do not have: `SYS_PTRACE`, which py-spy needs to read the memory of the
Odoo processes. It is contained like this:

* The sidecar joins only the PID namespace of the instance's own Odoo container, after the panel checked that
  container's name and labels; it never sees the server's processes or other instances.
* It has no network (`--network none`), every other capability dropped, `no-new-privileges`, 256 MB of memory, one
  processor and 64 processes, and is removed as soon as the recording is copied out.
* Its image is built from `python:3.12-slim` and one pinned py-spy wheel checked against its sha256, or is the image
  the administrator set in `PROFILER_IMAGE`; nothing is downloaded at run time.
* A recording holds function names, files and line numbers, never variables or data; the processes' command lines,
  which may contain passwords, are removed before it is stored (mode 600).
* Recording needs `instances.profile` in the instance; production is confirmed every time and can be switched off
  (`PROFILER_PRODUCTION`); starting, stopping, downloading and deleting are in the audit log.
