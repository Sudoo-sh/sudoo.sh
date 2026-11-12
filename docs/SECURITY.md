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
10. [Checklist for the administrator](#10-checklist-for-the-administrator)

---

## 1. What we protect, and from whom

| What | Why it matters |
| :--- | :--- |
| The panel | It controls Docker on the server: an administrator account can do anything on the server. |
| Panel passwords | People reuse passwords; a leaked one could open other doors. |
| GitHub deploy keys | They read the customers' private code. |
| The server | It runs every instance. |

| From whom | Typical attempt | Main defence |
| :--- | :--- | :--- |
| Anyone on the internet | Guessing passwords on the sign-in page | Slow hashes, sign-in lockout, strong generated passwords |
| A website the user visits | Making the user's browser send requests to the panel | Same-origin check, CSRF token, JSON-only, `SameSite=Strict` cookie |
| Somebody typing into a form | Text that becomes a command, a path or a script | Allow-list checks, no shell, escaping, Content-Security-Policy |
| A sync operator | Doing administrator actions | Roles checked on the server for every request |
| Someone with a copy of the files (a backup, a copied folder) | Reading passwords and keys | Hashes and encryption with a key kept apart |

Examples of each attempt, and how the panel stops it, are in [Web attacks](concepts/WEB_ATTACKS.md).

---

## 2. Reachability

* With the default exposure plugin (`cloudflare-quick`), the panel (`127.0.0.1:9000`), every gateway and every Odoo
  listen on `127.0.0.1` only. Nothing is reachable from the internet except through the Cloudflare connectors that
  the server opened itself, and SSH for administrators.
* With `host-ports`, the panel and the gateways listen on every interface, on purpose; Odoo itself and the
  databases still listen on `127.0.0.1` only. Use a firewall to limit who can reach the ports, and https in front of
  them on any network you do not fully trust (see [Plugins](PLUGINS.md#host-ports)).
* Cloudflare carries the traffic over https and decrypts it at its edge (that is how quick tunnels work); between
  Cloudflare and the server it travels inside the connector's own encrypted connection. See
  [Tunneling](concepts/TUNNELING.md#6-is-it-safe).
* The panel and every instance have different addresses (different browser origins), so an instance's pages can never read
  the panel's cookie. The gateway also removes the panel's cookie from requests to Odoo and stops Odoo from setting
  one with the same name.

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
| **Generated passwords** | New accounts and resets get 24 random characters (144 bits), shown once. |
| **Rules for chosen passwords** | 12 to 128 characters, at least 6 different characters, not containing the user name or a common word (`password`, `admin`, `odoo`, `123456`...), no control characters. |
| **Sign-in lockout** | 5 failures for an account from one address, or 20 from one address, block sign-in for 15 minutes. |
| **Changing or resetting a password** | Ends every other session of that account. |
| **Upgrade on sign-in** | Hashes made before the pepper existed are replaced with the current kind the next time their owner signs in. |

Why a hash and not encryption is explained in [Passwords and encryption](concepts/PASSWORDS_AND_ENCRYPTION.md).

### Second factors

A password can leak; a second factor means a leaked password alone does not open the panel.

| Factor | How it works |
| :--- | :--- |
| **Authenticator app** (`totp`) | The app and the panel share a secret (stored encrypted with the vault key) and both compute a 6-digit code every 30 seconds (RFC 6238). Each code works once; codes of the step before and after are accepted for clocks that are slightly off. |
| **E-mail code** (`email`) | A 6-digit code sent from the panel's mailbox (`SMTP_*`), valid 10 minutes, used once, kept only as a hash; 5 wrong tries use it up; a new one can be asked for once a minute. Setting it up confirms the address with a code. |

* After the password, the session only remembers who passed the first step, for 5 minutes; it opens nothing.
* Wrong codes count like wrong passwords (5 per account and address in 15 minutes, then blocked).
* `REQUIRE_SECOND_FACTOR` (`admins` by default) makes it compulsory: such an account without a factor can only set
  one up after signing in. Removing a factor needs the password and ends the account's other sessions.
* A lost phone: an administrator removes the account's factors (**Accounts**, or
  `DELETE /api/v1/users/<name>/second-factors`), which signs it out everywhere.
* More factors (SMS, hardware keys) can be added as plugins ([Plugins](PLUGINS.md#8-more-plugin-types)).

---

## 4. Stored secrets: the vault

Some secrets must be **read back** by the panel, so they cannot be hashed. They are **encrypted**:

| Secret | Stored as | Read when |
| :--- | :--- | :--- |
| GitHub deploy keys (`keys/deploy/*.key`) | AES-256-GCM, label `deploy-key:<owner>/<repo>` | For the length of one git connection, decrypted into a memory-only file readable by root, then deleted |
| Odoo master password of each instance (`MASTER_PASSWORD` in its `.env`) | AES-256-GCM, label `master-password:<name>` | When an administrator opens Technical details |

Backups (`/opt/odoo/backups`) contain whole databases, so they are mode 600 (root only), listed and downloaded only
by administrators, and only by their file name from the listing, never by a path.

* **AES-256-GCM** is authenticated encryption: besides hiding the value, it detects any change to it. A modified
  value is refused, never decrypted into garbage.
* Every value is encrypted together with a **label** saying what it is. A value copied to another place (for
  example one repository's key renamed as another's) fails to decrypt.
* The key is `/opt/odoo/manager/vault.key`: 32 random bytes, created on first use, mode 600. It is kept apart from
  the data: a copy of the instance folders or of the deploy keys alone is useless. Back it up **separately**
  ([Administrator guide: Backups](ADMIN_GUIDE.md#7-backups)).
* Odoo itself receives only a **hash** of its master password in `odoo.conf` (Odoo's own `pbkdf2-sha512` format).
  Odoo can check it but nobody can read it from that file.
* The cookie-signing key (`secret_key`) and the vault key are generated on the server and never leave it.

---

## 5. Sessions and the browser

| Measure | Effect |
| :--- | :--- |
| Signed session cookie `sudoo_session` | `HttpOnly` (scripts cannot read it), `Secure` (https only), `SameSite=Strict` (never sent with requests started by another site). |
| Session limits | Ends after 60 minutes without activity and after 12 hours in any case. |
| Session version | Each account has a version number in its record; a password change, reset or deletion raises it, and every older session stops working at once. |
| CSRF token | Every change must carry the session's secret token in a header, which another site cannot know. |
| Same-origin check | Every change must come from the panel's own address (`Origin` header). |
| JSON only | Every change must be sent as `application/json`; a form posted from another site cannot do that. |
| Content-Security-Policy | Only the panel's own scripts and styles run: no inline scripts or styles, no outside resources, no framing. An injected `<script>` would not run. |
| Other headers | `Strict-Transport-Security`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy` and `Cross-Origin-Resource-Policy: same-origin`, `Cache-Control: no-store` on pages and API answers. |
| Escaping | Everything shown in the browser is built from escaped text (`esc()` in `static/js/core.js`). |

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
| Instance and user names in addresses are checked before any file is touched | `/api/instances/..%2F..%2Fetc` |

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
| **An administrator account** | The panel controls Docker, so an administrator can do anything on the server. Give the administrator role only to people who would be trusted with the server. |
| **Plain http with `host-ports`** | With `PUBLIC_SCHEME=http`, passwords and session cookies travel unencrypted (the cookie is then not marked Secure). Put a TLS proxy in front, or keep it to trusted networks. |
| **Traffic inside Cloudflare** | Quick tunnels end https at Cloudflare's edge. For a server this is acceptable; Tailscale Funnel is the end-to-end alternative ([Tunneling](concepts/TUNNELING.md#7-other-ways-and-why-we-chose-this-one)). |
| **Each instance's database password** | Stored readable in the instance's `.env` and `odoo.conf` (folder mode 700, root only). The database is reachable only from that instance's Odoo container, on a private Docker network, so the password is of no use to anyone who could not already read the whole server. |
| **Sign-in lockout after a restart** | Failed attempts are counted in memory; a panel restart resets the counters. The slow hash still limits guessing. |
| **Odoo's own security** | Odoo inside an instance is the official image, unchanged; its own security is Odoo's. |

---

## 8. API tokens and webhooks

| Entry point | Who can use it | How it is checked |
| :--- | :--- | :--- |
| `/api/v1` (other systems) | holders of an API token **and** its signing secret | token (SHA-256 hash), HMAC-SHA256 signature over timestamp, nonce, method, path and body; timestamp within 5 minutes; each nonce accepted once; scope `read` or `write`; 5 refused calls from an address block it for 15 minutes |
| `/hooks/github` (when enabled) | GitHub | `X-Hub-Signature-256`: HMAC-SHA256 of the body with `GITHUB_WEBHOOK_SECRET` |

Every caller is also limited in requests per minute (`RATE_LIMIT_READS_PER_MINUTE`, `RATE_LIMIT_CHANGES_PER_MINUTE`,
per user, token or address), and instance creation can be limited in number and frequency (`MAX_INSTANCES`,
`MAX_INSTANCES_PER_USER`, `CREATE_COOLDOWN_MINUTES`); see [Configuration](CONFIGURATION.md#limits).

The signing secret is stored encrypted (vault), the token only as a hash; both are shown once. API tokens do not open the
panel's pages; the panel's session opens the API only from the panel's own address, with its CSRF token. Details: [Panel API](PANEL_API.md#6-security-notes).

---

## 9. Roles and the audit log

* Roles are checked **on the server** for every request; hiding a button in the browser is only a convenience.
* Sync operators never receive passwords, master passwords or ports, even in API answers.
* The audit log (in `/opt/odoo/manager/sudoo.db`, shown under **Audit log**) records sign-ins, failed sign-ins,
  lockouts, refused actions, every action on an instance, and account and key changes, with time, account,
  address and result. Passwords are never written to it.

---

## 10. Checklist for the administrator

- [ ] Store the `admin` and `sync` passwords from the installer in a password manager.
- [ ] Back up `vault.key` separately from other backups.
- [ ] Give the administrator role only to people trusted with the server.
- [ ] Delete accounts of people who no longer need them; reset a password when in doubt.
- [ ] Look at the audit log from time to time for refused actions and failed sign-ins.
- [ ] Keep real customer data out of instances.
- [ ] Remove deploy keys of repositories that are no longer used, in the panel and on GitHub.
- [ ] Give each system its own API token, `read` where reading is enough; revoke tokens no longer used.
