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
8. [Roles and the audit log](#8-roles-and-the-audit-log)
9. [Checklist for the administrator](#9-checklist-for-the-administrator)

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

* The panel (`127.0.0.1:9000`), every gateway and every Odoo listen on `127.0.0.1` only. Nothing is reachable from
  the internet except through the Cloudflare connectors that the server opened itself, and SSH for administrators.
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
| **Pepper** | A secret mixed in before hashing, taken from `vault.key`, which is not in `users.json`. A stolen `users.json` alone cannot even be attacked. |
| **Constant-time comparison** | Checking a password takes the same time whether it is almost right or completely wrong, and for unknown accounts too: timing reveals nothing. |
| **Generated passwords** | New accounts and resets get 24 random characters (144 bits), shown once. |
| **Rules for chosen passwords** | 12 to 128 characters, at least 6 different characters, not containing the user name or a common word (`password`, `admin`, `odoo`, `123456`...), no control characters. |
| **Sign-in lockout** | 5 failures for an account from one address, or 20 from one address, block sign-in for 15 minutes. |
| **Changing or resetting a password** | Ends every other session of that account. |
| **Upgrade on sign-in** | Hashes made before the pepper existed are replaced with the current kind the next time their owner signs in. |

Why a hash and not encryption is explained in [Passwords and encryption](concepts/PASSWORDS_AND_ENCRYPTION.md).

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
| Signed session cookie `oim_session` | `HttpOnly` (scripts cannot read it), `Secure` (https only), `SameSite=Strict` (never sent with requests started by another site). |
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

Every value received from a browser or API client goes through `instance_manager/web/validation.py` before it is used:

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
(`shell.py`, `jobs.py`), and git commands put `--` before addresses, so nothing typed can become a command or an
option. Refused input gets HTTP 400 with a sentence saying what to enter instead.

---

## 7. What is not protected

Being clear about limits is part of security.

| Not protected | Why, and what to do |
| :--- | :--- |
| **Instance contents** | Every instance opens with `admin` / `admin` and anyone with its link can sign in. Instances must not hold real customer data. |
| **Someone with root on the server** | Root can read `vault.key` as well as the encrypted values, and controls Docker. The vault protects copies and backups, not a compromised server. Keep SSH access to the server limited. |
| **An administrator account** | The panel controls Docker, so an administrator can do anything on the server. Give the administrator role only to people who would be trusted with the server. |
| **Traffic inside Cloudflare** | Quick tunnels end https at Cloudflare's edge. For a server this is acceptable; Tailscale Funnel is the end-to-end alternative ([Tunneling](concepts/TUNNELING.md#7-other-ways-and-why-we-chose-this-one)). |
| **Each instance's database password** | Stored readable in the instance's `.env` and `odoo.conf` (folder mode 700, root only). The database is reachable only from that instance's Odoo container, on a private Docker network, so the password is of no use to anyone who could not already read the whole server. |
| **Sign-in lockout after a restart** | Failed attempts are counted in memory; a panel restart resets the counters. The slow hash still limits guessing. |
| **Odoo's own security** | Odoo inside an instance is the official image, unchanged; its own security is Odoo's. |

---

## 8. Roles and the audit log

* Roles are checked **on the server** for every request; hiding a button in the browser is only a convenience.
* Sync operators never receive passwords, master passwords or ports, even in API answers.
* The audit log (`/opt/odoo/manager/audit.log`, shown under **Audit log**) records sign-ins, failed sign-ins,
  lockouts, refused actions, every action on an instance, and account and key changes, with time, account,
  address and result. Passwords are never written to it.

---

## 9. Checklist for the administrator

- [ ] Store the `admin` and `sync` passwords from the installer in a password manager.
- [ ] Back up `vault.key` separately from other backups.
- [ ] Give the administrator role only to people trusted with the server.
- [ ] Delete accounts of people who no longer need them; reset a password when in doubt.
- [ ] Look at the audit log from time to time for refused actions and failed sign-ins.
- [ ] Keep real customer data out of instances.
- [ ] Remove deploy keys of repositories that are no longer used, in the panel and on GitHub.
