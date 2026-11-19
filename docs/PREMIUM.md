# Premium features

**Audience:** super admins and administrators of the panel, and administrators of the server.
**Covers:** which features are premium, how they install and update on their own, what you see in Settings, what
happens when your plan changes, how a premium package is checked and tied to your server, and what to do when one
does not install.

---

## Contents

1. [What premium features are](#1-what-premium-features-are)
2. [How they install](#2-how-they-install)
3. [Settings › Plan › Premium features](#3-settings--plan--premium-features)
4. [When your plan changes](#4-when-your-plan-changes)
5. [How a premium package is checked](#5-how-a-premium-package-is-checked)
6. [Moving to another server](#6-moving-to-another-server)
7. [Settings](#7-settings)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. What premium features are

The panel works fully on every plan. Some features come with paid plans and install automatically from Sudoo.sh
when your plan includes them:

| Feature | Plans |
| :--- | :--- |
| Teams and access per project and per branch | Pro and Business |
| Custom domains (your own names, with certificates) | Business |
| Off-site copies of backups | Pro and Business |
| Support access (letting Sudoo.sh support in for a while) | Pro and Business |
| Several servers | Business |

Managed names under Sudoo.sh's own domain, branch locks, backups on the server and everything else in these
guides are part of every plan.

While a premium feature is not installed, its page or section shows a short card that says which plans include it.
Anything an API call to it would do answers `402` with the same sentence.

## 2. How they install

You do nothing. The panel must be linked to your Sudoo.sh account (Settings › Marketplace, `MARKETPLACE` on). After
each signed lease from Sudoo.sh (the heartbeat, every 15 minutes), the panel compares what your plan includes with
what is installed. When something is missing or a newer version exists, it starts the job **Install premium
features**: it downloads each package, checks it (section 5), unpacks it and restarts the backend once no other job
runs. Instances keep running during the restart.

Updates follow the same way: the panel asks for newer versions every `PREMIUM_CHECK_HOURS`.

To install by hand instead, turn off `PREMIUM_AUTO_INSTALL` and press **Check now** when you are ready.

## 3. Settings › Plan › Premium features

The card lists every premium feature with its state:

- **Installed** with its version: it runs.
- **Waiting**: your plan includes it and it installs with the next check (or press **Check now**).
- **Restart pending**: installed, it loads when the backend restarts in a moment.
- **Failed**: the reason is shown (for example, no build for this server yet, or Sudoo.sh could not be reached).
  The panel tries again after `PREMIUM_RETRY_MINUTES`.
- **Switched off**: your plan no longer includes it (section 4).
- **Not in your plan**: with the plans that include it.

**Check now** (permission `settings.manage`) asks Sudoo.sh at once and shows the job.

## 4. When your plan changes

When your plan no longer includes a premium feature, the panel backs up its own data first, then switches the
feature off and e-mails the super admins. Nothing is deleted: its data and settings stay, and when your plan
includes it again it comes back on by itself.

What was set up keeps working where the panel can do it on its own:

- restrictions and locks on projects and branches stay enforced, and you can always remove them;
- teams stay named in the access they were given, and you can always take members out or delete a team;
- custom domains keep being served with the certificates they have (they are not renewed while the feature is off);
- off-site copies already made stay where they are.

## 5. How a premium package is checked

- It is downloaded from Sudoo.sh with a signed request, and its SHA-256 must be the one Sudoo.sh states.
- It must carry a signature of Sudoo.sh's premium key, a key used for premium packages only (a marketplace plugin's
  signature is never accepted for one). Rotations of that key are accepted only when a key the panel already trusts
  endorsed the new one.
- It must carry a binding to your server: your installation, your server's hardware fingerprint, the package and
  version, and an end date. The panel renews the binding before it ends (`PREMIUM_BINDING_RENEW_DAYS`).
- It may contain compiled modules, built for the panel's own Python; marketplace plugins may not.
- It may only use the extension points made for premium features.

The panel checks the files, the signature and the binding when it starts and every `PREMIUM_WATCH_MINUTES`. A
package whose files changed, whose binding ended or that is on another machine is not loaded. Its web parts are
served to the browser only while each file still has the hash its signed manifest states.

## 6. Moving to another server

A premium package is tied to the server it was installed on. If you move the panel's folder to another machine,
the premium features stop there until Sudoo.sh accepts the new machine: accept it on app.sudoo.sh (Servers), then
press **Check now**; the panel downloads a fresh binding.

## 7. Settings

All are on **Settings**, group **Premium features**, except `PREMIUM_PUBLIC_KEYS`
([Configuration](CONFIGURATION.md#premium-features)).

| Setting | Default | Meaning |
| :--- | :--- | :--- |
| `PREMIUM_AUTO_INSTALL` | on | Install and update what the plan includes, on its own. |
| `PREMIUM_CHECK_HOURS` | 6 | Hours between checks for newer versions. |
| `PREMIUM_RETRY_MINUTES` | 15 | Minutes before asking again after a check. |
| `PREMIUM_WATCH_MINUTES` | 10 | Minutes between integrity passes. |
| `PREMIUM_BINDING_RENEW_DAYS` | 7 | Days before a binding ends that a new one is asked for. |
| `PREMIUM_MAX_PACKAGE_MB` | 60 | The largest package downloaded. |
| `PREMIUM_PUBLIC_KEYS` | empty | `.env` only, for test panels: the premium keys to trust instead of the shipped ones. |

## 8. Troubleshooting

| You see | Do this |
| :--- | :--- |
| Every feature says **Not in your plan** | Link the panel to your account (Settings › Marketplace) and check your plan on app.sudoo.sh. |
| **Failed**: no build for this server | Update Sudoo.sh; builds follow each release. |
| **Failed**: installed on another machine | Accept this machine on app.sudoo.sh (Servers), then **Check now**. |
| **Failed**: its files were changed | Press **Check now**: the panel installs it again. |
| **Restart pending** for a long time | A long job is running; the backend restarts when it ends. |
