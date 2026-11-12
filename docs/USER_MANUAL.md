# User Manual

**Audience:** everyone who uses the Sudoo.sh panel in a browser: super admins, administrators, maintainers,
developers and viewers.
**Covers:** the first run, signing in, every page and button of the panel, roles, backups, settings, step by step.
**You need:** the panel's link and an account (or, on a new server, the setup code). Nothing has to be installed on
your computer.

---

## Contents

1. [First run and signing in](#1-first-run-and-signing-in)
2. [The Instances page](#2-the-instances-page)
3. [An instance's page](#3-an-instances-page)
4. [Creating an instance](#4-creating-an-instance)
5. [Keeping the code up to date](#5-keeping-the-code-up-to-date)
6. [Submodules](#6-submodules)
7. [GitHub access (private repositories)](#7-github-access-private-repositories)
8. [Accounts and roles](#8-accounts-and-roles)
9. [My account](#9-my-account)
10. [Backups and audit log](#10-backups-and-audit-log)
11. [Settings](#11-settings)
12. [Help pages and appearance](#12-help-pages-and-appearance)
13. [When something goes wrong](#13-when-something-goes-wrong)
14. [Questions and answers](#14-questions-and-answers)

Words used in this manual:

| Word | Meaning |
| :--- | :--- |
| **Instance** | One Odoo with its own database and its own link. |
| **Panel** | This website, where instances are managed. |
| **Repository** | The GitHub project that holds the custom modules of an instance. |
| **Branch** | A line of work inside a repository, for example `main` or `19.0`. |
| **Link** | The public https address of an instance, for example `https://river-stone-apple.trycloudflare.com`. |
| **Job** | One action on an instance (create, get latest code, restart...), with its steps and log. |

The panel only shows the pages and buttons your role may use ([section 8](#8-accounts-and-roles)). If something
described here is missing for you, your role does not include it.

---

## 1. First run and signing in

### The first run: Set up Sudoo.sh

A new server has no accounts. The first person to open the panel's link sees the **Set up Sudoo.sh** page and
creates the first account, which is a **Super admin**.

1. Ask the server administrator for the **setup code**. The installer prints it (it looks like `ABCD-EFGH-JKLM`);
   it can be shown again on the server with `docker exec sudoo-panel sudoo.sh setup-code`.
2. Open the panel's link and fill in:

   | Field | What to enter |
   | :--- | :--- |
   | **Setup code** | The code from the installer. |
   | **First name**, **Last name**, **E-mail** | Your details. The e-mail address can later receive sign-in codes. |
   | **Phone** | Optional. |
   | **User name** | What you sign in with, in lower case. |
   | **Password**, **Password again** | At least 12 characters. A few unrelated words make a strong one. |

3. Click the button at the bottom. The account is created and you are signed in.

The setup code works once: after the first account exists, the page no longer appears. After 5 wrong codes within
15 minutes, the address you use is blocked for 15 minutes.

### Signing in

1. Open the panel's link.
2. Enter your **user name** and **password**, then click **Sign in**.
3. If your account has a second factor, the **Enter your code** step follows:
   * **E-mail code**: a 6-digit code is sent to your e-mail address at once. **Send a new code** becomes available
     after 60 seconds.
   * **Authenticator app**: open the app and type the 6-digit code shown for Sudoo.sh.
   * When your account has both, switch between them with the buttons above the code.
   * Type the code (it is checked as soon as the sixth digit is in) or click **Verify and sign in**. **Back**
     returns to the user name and password.

### Before anything else opens

Two things can be asked of you right after signing in. Until they are done, no other page opens.

| Situation | What you do |
| :--- | :--- |
| Your account is new, or your password was reset | **Choose your password**: enter the password you were given, then your own new password twice. |
| The server requires a second factor for your role, and you have none | **Set up a second factor**: an authenticator app or an e-mail code ([section 9](#9-my-account)). |

By default super admins and administrators must use a second factor. A super admin can change this
(**Settings, Sign-in and security**).

### Good to know

* After **5 wrong passwords** for the same account from the same place, or 20 from the same place, signing in is
  blocked for **15 minutes**. Wait, or ask an administrator to reset your password.
* You are signed out after **60 minutes without activity**, and after **12 hours** in any case. A super admin can
  change both times on the Settings page.
* **Sign out** (your name at the bottom of the sidebar, or **My account**) ends the session at once. Use it on
  shared computers.
* The panel's link can change when the server restarts. If the link you saved stops working, ask the administrator
  for the new one ([why links change](concepts/TUNNELING.md#5-why-the-link-changes)).

---

## 2. The Instances page

This is the first page after signing in. The sidebar on the left leads to the other pages:

| Sidebar | Pages | Shown to |
| :--- | :--- | :--- |
| Top | **Instances**, **New instance**, **Help** | everyone (New instance: roles that may create instances) |
| **Operations** | **Backups**, **GitHub access**, **Audit log** | administrators and super admins; GitHub access also to maintainers |
| **Administration** | **Settings**, **Accounts**, **API tokens** | Settings: super admins; Accounts and API tokens: administrators and super admins |
| Bottom | your name and role (opens **My account**), the theme button | everyone |

On a small screen the sidebar opens with the menu button at the top left.

**Server figures** (the four tiles at the top):

| Tile | What it shows |
| :--- | :--- |
| Processor | How busy the server's processor is. |
| Memory | Memory in use on the server. |
| Disk | Disk space in use on the server. |
| Running | How many instances are running now. |

Below the tiles, a **live chart** shows the server's processor and memory over the last hour, and **Busiest now**
lists the instances that use the most right now.

**The list of instances.** Use **Search** to find an instance by name, and the filters **All**, **Online**,
**Paused** and **Working** (each with its count). Each instance has a card with:

* its name and status ([statuses](#statuses)),
* its Odoo version, edition (Community or Enterprise) and branch,
* the last commit, or, while a job runs, that job with its progress,
* processor, memory and requests per minute,
* **Open**, which opens Odoo in a new tab.

Click the card to open the instance's page.

The page updates by itself: new instances, status changes and job progress appear without reloading. When the
connection to the server is lost for a moment, a small **Reconnecting** notice shows until it is back.

---

## 3. An instance's page

### The header

At the top: the instance's name and status, its Odoo version, edition, branch and link. While a job runs, a line
below the header shows it with its progress; click it to follow the steps.

A warning appears when the instance's branch no longer exists on GitHub, or when its link has a problem.

### Buttons

| Button | What it does |
| :--- | :--- |
| **Open Odoo** | Opens the instance in a new tab. Sign in with the login on the **Access** tab (or ask someone who may see it). Not shown while the instance is paused, switched off or stopped. |
| **Start** | Shown when the instance is paused, switched off or stopped. Starts it now. |
| **Restart** | Shown while it runs. Stops and starts Odoo (about 30 seconds). Useful when Odoo behaves strangely. |
| **Get latest code** | Downloads the newest code of the branch from GitHub, upgrades the modules that changed and restarts. |
| **...** (menu) | The actions below. |

The menu:

| Action | What it does |
| :--- | :--- |
| **Update submodules** | Moves every submodule to the newest commit of its branch and restarts ([section 6](#6-submodules)). |
| **Upgrade modules** | Like **Upgrade** in Odoo's Apps menu. Enter technical names separated by commas, or `all`. |
| **Switch branch** | Moves the instance to another branch of the same repository, upgrades its modules and restarts. The database stays. |
| **Back up now** | Saves the database and the files as an Odoo backup, listed under **Backups**. A paused instance stays paused. |
| **Pause** | Stops it to free memory now. It starts again on the next visit. |
| **Switch off** | Stops it completely. Visitors see that it is switched off, and it does not start until someone clicks **Start**. |
| **Delete** | Removes Odoo, its database and its files, after a backup. Type the instance's name to confirm. |

Each button and menu item is shown only to roles that may use it. While a job runs on the instance, the buttons
wait: one job per instance at a time.

### Statuses

| Status | Meaning | What to do |
| :--- | :--- | :--- |
| **Online** | Odoo answers. | Nothing. |
| **Starting** | The container runs but Odoo is not answering yet. | Wait up to a minute. If it stays, open the **Odoo log** tab. |
| **Working** | A job (update, restart, backup...) is running. Other actions wait their turn. | Wait; click the job line to follow it. |
| **Paused** | Stopped to save memory because nobody used it for some hours (6 by default). | Nothing: it starts by itself when someone opens its link, or click **Start**. |
| **Switched off** | Stopped on purpose. It does **not** start when someone opens it. | Someone allowed to do so clicks **Start**. |
| **Stopped** | Odoo stopped unexpectedly, often for lack of memory. | **Start**. If it happens again, check the **Odoo log** and pause other instances. |
| **Checking** | The panel has not measured the instance yet, for example just after the panel started. | Wait a few seconds. |

### Tabs

| Tab | Contents |
| :--- | :--- |
| **Overview** | Processor, memory, requests per minute and response time of this instance; one live chart with a **CPU / Memory / Requests / Response** switch; details such as the database, its size and how long it has been running. |
| **Activity** | Every job on this instance, newest first. Click one to open a panel with its steps, notes, the reason it failed (if it did) and the technical log, which follows live while the job runs. |
| **Code** | The repository and branch, the recent commits, and the submodules, with **Add submodule** and **Remove** ([section 6](#6-submodules)). |
| **Odoo log** | The latest lines Odoo wrote (100 to 3000), with **Refresh**. Lines with `ERROR` or `Traceback` usually explain a problem. |
| **Settings** | The automation switches and the lifetime ([section 5](#5-keeping-the-code-up-to-date)). Changes are saved at once. |
| **Access** | The Odoo sign-in: address, login, password, and the master password (for Odoo's database manager). Click **Show the passwords**; each viewing is recorded in the audit log. |

---

## 4. Creating an instance

Click **New instance** in the sidebar (roles that may create instances).

1. **GitHub repository**: paste the repository's address. On GitHub, open the repository, click the green **Code**
   button and copy the address, for example `https://github.com/acme-co/sales`. Leave it empty for a plain Odoo
   without custom modules.
2. The panel checks the repository while you type. The hint under the field says how many branches it found, or
   what is wrong.
3. **Branch**: pick the branch. The instance only ever takes code from this branch. When the branch is named like a
   version (`19.0`), the Odoo version follows it.
4. **Name**: leave it empty to use repository and branch. The hint shows the final name and the database name, for
   example "Will be **sales-main** · database `sales_main`".
5. Below the name, the **access report** shows one line for the repository and one for every submodule, each with
   a mark: green when the server can read it, red with the reason when it cannot. For a private repository, add a
   deploy key under **GitHub access** ([section 7](#7-github-access-private-repositories)); the report is checked
   again when you come back.
6. **Version**: the Odoo versions offered on this server.
7. **Edition**: **Community** (Odoo's free edition) or **Enterprise** (Enterprise apps and interface). Enterprise
   can be chosen only for the versions whose Enterprise add-ons are on the server.
8. **Options**:

   | Option | Effect |
   | :--- | :--- |
   | **Install the repository's modules** | Installs the repository's own modules (not those of submodules) after the database is created. |
   | **Load demo data** | Sample customers, products and orders, useful for presentations. |
   | **Get new code automatically** | New commits on the branch are applied by themselves ([section 5](#5-keeping-the-code-up-to-date)). |
   | **Pause when nobody uses it** | The instance pauses after some hours without visits, to free memory. |
   | **Lifetime** | Deleted that many days after creation (30 by default), with a backup first; or **Keep until deleted by hand**. |

9. Click **Create instance**.

If the server has a limit on the number of instances, or a waiting time between two new instances, a warning above
the form says so before you start, and the numbers under the button show how many exist.

The new instance's page opens and shows each step: preparing, downloading the code, downloading Odoo (the first time
for a version takes a few minutes), starting the database, creating the Odoo database, installing modules,
creating the public link, starting Odoo. A new instance usually takes **2 to 4 minutes**. You can leave the page:
the work continues and is listed under **Activity**.

When it is ready, the link is in the header and the login is on the **Access** tab.

If a step fails, the job explains what went wrong in plain words, with the technical log below. Everything created
so far is removed, so you can simply try again.

---

## 5. Keeping the code up to date

**Automatically.** When **Get new code automatically** is on, the panel checks GitHub for new commits on the
instance's branch (every 2 minutes by default, or at once when the server receives GitHub's webhooks). When there
are new commits, it downloads them, upgrades the modules whose files changed and restarts Odoo. The job appears in
**Activity** as "Get latest code", started automatically.

**By hand.** Click **Get latest code**. Use it right after a developer says "I pushed".

**Upgrade modules.** A new field, view or data file needs the module to be upgraded in Odoo. This happens after an
update when **Upgrade changed modules** is on. Otherwise use **Upgrade modules** in the menu, with the technical
names separated by commas, or `all`.

**Switch branch.** Moves the instance to another branch of its repository (menu, **Switch branch**). Use it when the
branch was merged and deleted on GitHub, or to show other work in the same database.

**Settings tab** of an instance:

| Setting | Effect |
| :--- | :--- |
| **Get new code automatically** | Off: the instance only changes when someone clicks **Get latest code**. Use it for an instance that must not change before a presentation. |
| **Upgrade changed modules** | On: after an update, the modules whose files changed are upgraded in Odoo. Off: Odoo only restarts. |
| **Pause when idle** | Off: the instance never pauses by itself (it keeps using memory). |
| **Lifetime** | The number of days after creation when the instance is deleted, after a backup; or **Keep until deleted by hand**. |

Changes on this tab are saved at once.

---

## 6. Submodules

A submodule is another repository included inside the instance's repository, for example OCA's `web` modules in a
folder `oca/web`. See [Git submodules](concepts/GIT_SUBMODULES.md) for how they work.

The **Code** tab lists the submodules with their folder, address and current version.

| Action | Where | What it does |
| :--- | :--- | :--- |
| **Update submodules** | menu at the top | Moves every submodule to the newest commit of its branch and restarts. |
| **Add submodule** | Code tab | Adds a repository as a submodule and saves the change to GitHub. |
| **Remove** | next to a submodule | Removes it from the instance and from GitHub. Uninstall its modules in Odoo first. |

Adding a submodule:

1. Click **Add submodule**.
2. **Repository**, for example `https://github.com/OCA/web`.
3. **Folder** (optional, for example `oca/web`; empty: the repository's name) and **Branch**. For OCA repositories
   use the branch that matches the Odoo version, for example `19.0`.
4. Confirm. The panel first checks that it can read the repository.

Adding and removing change the repository on GitHub, so the server's key for the main repository must be allowed
to write ("Allow write access" ticked on GitHub). If it is not, GitHub refuses, the panel undoes the change on the
server and tells you so: the server and GitHub never disagree.

---

## 7. GitHub access (private repositories)

Public repositories need nothing. Every **private** repository, including every private submodule, needs a
**deploy key**: a key that lets the server read that one repository and nothing else. See
[Deploy keys](concepts/DEPLOY_KEYS.md) for how they work.

You need **admin rights on the repository on GitHub** to add a key. If you do not have them, send the key to
someone who does.

1. In the panel, open **GitHub access** (sidebar, Operations) and click **Add a repository**.
2. Enter the repository (`https://github.com/owner/repository` or `owner/repository`) and click **Create key**.
3. The panel shows the public key. Copy it, then click **Open GitHub** (the repository's **Settings, Deploy keys,
   Add deploy key** page).
4. On GitHub:
   * **Title**: `Sudoo.sh`
   * **Key**: paste.
   * **Allow write access**: leave unticked, unless submodules of this repository will be added or removed from
     the panel.
   * Click **Add key**.
5. Back in the panel, click **Done**. On the **New instance** page the repository's line now has a green mark.

Only the public part of the key leaves the server; the private key is stored encrypted.

The **GitHub access** page lists all keys with their repository and creation date. **Delete** removes the server's
key; also remove it on GitHub (repository Settings, Deploy keys). One key per repository is shared by every
instance that uses that repository.

---

## 8. Accounts and roles

### Roles

Every account has one role. The role decides which pages, buttons and menu items the account sees.

| What | Super admin | Administrator | Maintainer | Developer | Viewer |
| :--- | :---: | :---: | :---: | :---: | :---: |
| See instances, their activity, figures and Odoo log | yes | yes | yes | yes | yes |
| Read the Help pages; change own profile, password and second factors | yes | yes | yes | yes | yes |
| Get latest code, update submodules, add a submodule | yes | yes | yes | yes | no |
| Restart, start | yes | yes | yes | yes | no |
| Create instances | yes | yes | yes | no | no |
| Pause, switch off, upgrade modules, switch branch, back up now, remove a submodule | yes | yes | yes | no | no |
| Change an instance's settings tab (automation, lifetime) | yes | yes | yes | no | no |
| See the Odoo passwords (Access tab) | yes | yes | yes | no | no |
| GitHub access (deploy keys) | yes | yes | yes | no | no |
| Delete instances | yes | yes | no | no | no |
| Backups (download, delete) | yes | yes | no | no | no |
| Accounts, API tokens, audit log | yes | yes | no | no | no |
| Create, change and delete super admins | yes | no | no | no | no |
| Settings, restart the panel | yes | no | no | no | no |

API tokens act with a role too: a **write** token as an Administrator, a **read** token as a Viewer.

When you try something your role does not allow, the panel says which permission is missing. Ask an administrator.

Accounts from an earlier version of the panel were renamed automatically: "administrator" became **Super admin**
and "sync operator" became **Developer**.

### The Accounts page

**Accounts** (sidebar, Administration) lists every account with its role, whether it has a second factor and its
last sign-in.

| Action | What happens |
| :--- | :--- |
| **New account** | Enter first name, last name, e-mail and phone (optional), a **user name** (3 to 32 lower-case letters, digits, dots and dashes, starting with a letter) and a **role**. A password is generated and shown **once**: copy it and give it to the person through a safe channel. At the first sign-in they must choose their own. |
| **Edit** (row menu) | Change the profile and the role. |
| **Reset password** | A new password is generated and shown once. The account is signed out everywhere and must choose a new password at the next sign-in. |
| **Remove second factors** | For a person who lost their phone or mailbox. They sign in with the password only, and set up a new second factor if one is required. |
| **Delete** | The account is removed and signed out at once. |

Rules: you cannot change or delete your own account here (use **My account**), only a super admin can create,
change or delete a super admin, and the last super admin cannot be deleted or given another role.

Passwords are never stored by the panel, only checked: see
[Passwords and encryption](concepts/PASSWORDS_AND_ENCRYPTION.md).

### API tokens

**API tokens** (sidebar, Administration) let other systems, such as a CI pipeline, create and manage instances
through the panel's API. Click **New token**, give it a name and a scope (**read**, or **write**); copy the token
and the signing secret, shown once, into the other system. The list shows when each token was last used.
**Revoke** stops a token at once. See [Panel API](PANEL_API.md).

---

## 9. My account

Click your name at the bottom of the sidebar.

**Profile.** First name, last name, e-mail and phone. Click **Save**. While your e-mail address receives your
sign-in codes it is locked here; to change it, remove the e-mail code and set it up again with the new address.

**Password.** Enter your current password and the new one twice, then click **Change password**. The new password
must:

* be 12 to 128 characters long,
* use at least 6 different characters,
* not contain your user name,
* not contain a common word or sequence such as `password`, `admin`, `odoo` or `123456`.

A short sentence is the easiest kind of password to remember and the hardest to guess, for example
`green tea at 7 on the balcony`. Changing your password signs out your other sessions.

**Second factor.** A second factor asks for a 6-digit code after the password, so a stolen password alone is not
enough.

| Factor | Setting it up |
| :--- | :--- |
| **Authenticator app** | Click **Set up**, scan the QR code with an app such as Google Authenticator, Microsoft Authenticator or 1Password, and type the code it shows. |
| **E-mail code** | Click **Set up**, check the address, click **Send a code**, and type the code from the e-mail. Only offered when the server can send e-mail. |

**Remove** turns a factor off; type your password to confirm. Your other sessions end. If your role requires a
second factor, keep at least one.

---

## 10. Backups and audit log

### Backups

**Backups** (sidebar, Operations) lists every backup: instance, database, when it was taken and its size.

* A backup is taken **before every deletion**, automatic or by hand. If the backup fails, the instance is not
  deleted.
* **Back up now** (menu on an instance's page) takes one at any time.
* Backups are kept until someone deletes them, so look at the list from time to time; the disk usage is on the
  Instances page.

What a backup contains: one zip file in Odoo's own backup format, with

| Part | Contents |
| :--- | :--- |
| `dump.sql` | The whole database. |
| `filestore/` | The attachments and images that Odoo keeps outside the database. |
| `manifest.json` | The database name and the Odoo version. |

**Download** saves the zip. **Delete** removes a backup you no longer need.

To restore a backup:

1. Open an Odoo of the **same version** (an instance's link, or any other Odoo) at `/web/database/manager`.
2. Click **Restore Database**.
3. Enter the master password (on the instance's **Access** tab), choose the zip file and a database name.
4. Say whether the database was **moved** (it replaces the original) or is a **copy** (a second database next to
   the original), then confirm.

Restoring brings back both the data and the attachments, because both are in the zip.

### Audit log

**Audit log** (sidebar, Operations) lists what happened, newest first: sign-ins (also failed ones), refused actions,
every job on an instance, viewing an instance's passwords, account, key, token and settings changes. Each line shows
when, the account, what happened, the instance and the address it came from. Use **Search** to find entries,
**Refused only** to see only what was not allowed, and **Show older entries** to go further back.

---

## 11. Settings

Super admins only. **Settings** (sidebar, Administration) shows every setting of the panel that can be changed in
the browser, in groups:

| Group | What it holds |
| :--- | :--- |
| **Sign-in and security** | Who must use a second factor (none, administrators, everyone), and how long a session lasts. |
| **E-mail** | The mailbox that sends sign-in codes, with **Send a test**. |
| **Limits** | Requests per minute, how many instances in total and per person, and how often a new one can be created. 0 turns a limit off. |
| **New instances** | What a new instance starts with: pause when idle and after how many hours, automatic updates, upgrades, lifetime, Odoo login. |
| **Odoo versions and images** | Which versions are offered and where Odoo and PostgreSQL come from. |
| **Enterprise add-ons** | Where the Enterprise add-ons of each version are. |
| **Code updates and monitoring** | How often GitHub is checked, the webhook secret, how often figures are measured. |
| **Contact and appearance** | Who people are told to contact, and the accent colour of the panel and its e-mails. |
| **Plugins** | Which plugin does each job (mail, second factors, update triggers, Enterprise source, sign-in, rate limits). |
| **Server** | Read only: folders, ports and how the panel is reached. These stay in the server's `.env`. |

How changes apply:

* **At once.** Almost every change applies as soon as you save it. It is kept on top of the server's `.env` file.
* **Use .env** next to a changed setting brings back the value from `.env`.
* **Passwords** (mail password, Odoo admin password, webhook secret) are write-only: the page shows whether one is
  set, never the value. They are stored encrypted.
* **Plugins** apply after **Restart the panel** (in the Plugins group; the button is highlighted when a restart is
  waiting). The restart stops only the panel's backend for a few seconds; instances keep running. It is refused
  while jobs run.
* **Server** settings can only be changed by the server administrator, in `.env` on the server.

**Send a test** (E-mail group) sends a test message with the saved settings to an address you enter. If it fails,
the message says why; for Gmail, the password must be an app password
([Administrator guide: E-mail](ADMIN_GUIDE.md#6-e-mail)).

Every setting is described in [Configuration](CONFIGURATION.md).

---

## 12. Help pages and appearance

**Help** (sidebar) shows this documentation inside the panel: the same pages as in the project's `docs` folder,
with a table of contents and an **On this page** list of the current page's sections. Links between the pages work
as in the repository.

**Theme.** The button at the bottom of the sidebar switches between **System** (follows your computer's setting),
**Light** and **Dark**. The choice is kept in your browser. The accent colour is chosen by a super admin
(**Settings, Contact and appearance**).

---

## 13. When something goes wrong

1. Read the message: the panel explains failures in plain words.
2. Open the job in the instance's **Activity** tab to see which step failed, the reason and the technical log.
3. Try **Restart** if Odoo behaves strangely, and look at the **Odoo log** tab if it does not start.
4. If a button is missing or an action is refused, your role may not include it ([section 8](#8-accounts-and-roles)).
5. If it is still not solved, contact the administrator named on the sign-in page, and say which instance, what you
   did and what the message said.

Common messages are listed in the [Administrator guide's troubleshooting table](ADMIN_GUIDE.md#13-troubleshooting).

---

## 14. Questions and answers

**Do the people I show an instance to need an account?**
No. They only need the instance's link and the Odoo login. Panel accounts are only for people who manage instances.

**Can two instances be open in the same browser at the same time?**
Yes. Each instance has its own link, so each has its own Odoo sign-in.

**The instance shows "Starting up". Is it broken?**
No. It was paused because nobody used it for some hours. The page opens Odoo by itself after 20 to 60 seconds.

**I pushed to GitHub but the instance does not show my change.**
Wait 2 minutes or click **Get latest code**. If the change is a new field or view, the module must be upgraded:
check that **Upgrade changed modules** is on (Settings tab), or use **Upgrade modules**.

**The page says the branch no longer exists on GitHub.**
The branch was deleted on GitHub, usually after its pull request was merged, so there is nothing left to update
from. Use **Switch branch** in the menu and pick the branch the instance should follow now, usually `main`. The
database and its data stay as they are.

**I lost my phone with the authenticator app.**
Ask an administrator to use **Remove second factors** on your account. Then sign in with your password and set up a
new one.

**I forgot my password.**
Ask an administrator to use **Reset password**. You get a new password once and choose your own at the next sign-in.

**Does a backup include the attachments?**
Yes. The zip holds the database (`dump.sql`), the filestore with the attachments, and a manifest
([section 10](#10-backups-and-audit-log)).

**Can another system send webhooks to an instance, or call its API?**
Yes, through the instance's link. See [API and webhooks](API.md). Remember the link can change after a server
restart.

**Is my data safe in an instance?**
Instances are for showing and testing Odoo. Anyone with the link and the Odoo login can sign in. Do not put real
customer data in them.
