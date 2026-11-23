# User Manual

**Audience:** everyone who uses the Sudoo.sh panel in a browser: super admins, administrators, maintainers,
developers and viewers.
**Covers:** the first run, signing in, every page and button of the panel, roles, passwords, backups and restores,
settings, step by step.
**You need:** the panel's link and an account (or, on a new server, the setup code). Nothing has to be installed on
your computer.

---

## Contents

1. [First run and signing in](#1-first-run-and-signing-in)
2. [Projects](#2-projects)
3. [The Instances page](#3-the-instances-page)
4. [An instance's page](#4-an-instances-page)
5. [Creating an instance](#5-creating-an-instance)
6. [Keeping the code up to date](#6-keeping-the-code-up-to-date)
7. [Submodules](#7-submodules)
8. [GitHub access (private repositories)](#8-github-access-private-repositories)
9. [Accounts and roles](#9-accounts-and-roles)
10. [My account](#10-my-account)
11. [Backups and audit log](#11-backups-and-audit-log)
12. [Settings](#12-settings)
13. [Help pages and appearance](#13-help-pages-and-appearance)
14. [When something goes wrong](#14-when-something-goes-wrong)
15. [Questions and answers](#15-questions-and-answers)
16. [Client portal](#16-client-portal)

Words used in this manual:

| Word | Meaning |
| :--- | :--- |
| **Instance** | One Odoo with its own database and its own link. |
| **Panel** | This website, where instances are managed. |
| **Repository** | The GitHub project that holds the custom modules of an instance. |
| **Branch** | A line of work inside a repository, for example `main` or `19.0`. |
| **Link** | The public https address of an instance, for example `https://river-stone-apple.example.com`. |
| **Job** | One action on an instance (create, get latest code, restart...), with its steps and log. |

The panel only shows the pages and buttons your role may use ([section 9](#9-accounts-and-roles)). If something
described here is missing for you, your role does not include it.

---

## 1. First run and signing in

### The first run: Set up Sudoo.sh

A new server has no accounts. The first person to open the panel's link sees the **Set up Sudoo.sh** page and
creates the first account, which is a **Super admin**.

1. Ask the server administrator for the **setup code**. The installer prints it (it looks like `ABCD-EFGH-JKLM`);
   it can be shown again on the server with `docker exec sudoo-panel sudoo.sh setup-code`.
2. Open the panel's link. **Step 1 of 2, Licence agreement**: read the summary of the
   [Sudoo.sh Licence Agreement](https://sudoo.sh/legal/license) (**Read the full agreement** shows the whole text;
   **Open the published version** opens it on sudoo.sh). Choose how you will use Sudoo.sh:

   | Choice | When |
   | :--- | :--- |
   | **Personal projects** | Non-commercial only. No work for a business, employer or client. |
   | **My company** | Running Odoo for your company and its group companies only. |
   | **Service provider** | Running, hosting, managing or maintaining Odoo for other companies. |

   Tick **I have read and accept the Sudoo.sh Licence Agreement**; for **My company** and **Service provider** also
   tick **I accept it on behalf of my company, and I am authorised to bind it**. Click **Accept and continue**.
   Nothing is kept until the account is created; **Back** on the next step returns here.
3. **Step 2 of 2**: fill in:

   | Field | What to enter |
   | :--- | :--- |
   | **Setup code** | The code from the installer. |
   | **First name**, **Last name** | Your name. |
   | **E-mail** | Required. You sign in with it, and it can later receive sign-in codes. |
   | **Phone** | Optional. |
   | **Password**, **Password again** | 12 to 128 characters that follow the [password rules](#password-rules). A few unrelated words make a strong one. |

4. Click the button at the bottom. The account is created, your acceptance of the licence agreement is recorded, and
   you are signed in.

You do not choose a user name. The panel makes one from your e-mail address (`jane.doe@example.com` becomes
`jane.doe`; when it is taken, `jane.doe-2`, `-3` and so on). It is the account's internal key: it appears
in small monospace letters under your name, in the audit log and in the API. You can also sign in with it.

The setup code works once: after the first account exists, the page no longer appears. After 5 wrong codes within
15 minutes, the address you use is blocked for 15 minutes.

### Signing in

1. Open the panel's link.
2. Enter your **e-mail address** in the **E-mail** field and your **password**, then click **Sign in**. Your user
   name also works in this field, for example for an older account that has no e-mail address.
3. If your account has a second factor, the **Enter your code** step follows:
   * **E-mail code**: a 6-digit code is sent to your e-mail address at once. **Send a new code** becomes available
     after 60 seconds.
   * **Authenticator app**: open the app and type the 6-digit code shown for Sudoo.sh.
   * When your account has both, switch between them with the buttons above the code.
   * **Lost your phone?** (under the authenticator code) asks for one of your **recovery codes** instead
     ([section 10](#10-my-account)). Each code works once. **Use the app instead** goes back.
   * Type the code (it is checked as soon as the sixth digit is in) or click **Verify and sign in**. **Back**
     returns to the e-mail address and password.

### Before anything else opens

Three things can be asked of you right after signing in. Until they are done, no other page opens.

| Situation | What you do |
| :--- | :--- |
| Your account is new, or an administrator set a new password for you | **Choose your password**: enter the password you were given, then your own new password twice. An administrator who typed your password can leave this step out. |
| The server requires a second factor for your role, and you have none | **Set up a second factor**: an authenticator app or an e-mail code ([section 10](#10-my-account)). |
| You are a super admin, and nobody has accepted the licence agreement this version of the panel requires (a server set up before version 3.0, or a newer version of the agreement) | **Accept the licence agreement**: the same choices as at the first run. Until a super admin accepts, other super admins see this step too; administrators and everyone else carry on, and instances keep running and serving. |

By default super admins and administrators must use a second factor. A super admin can change this
(**Settings, Sign-in and security**).

### Good to know

* A wrong e-mail address and a wrong password give the same message: "Wrong e-mail address or password." The panel
  does not tell which of the two was wrong.
* After **5 wrong passwords** for the same account from the same place, or 20 from the same place, signing in is
  blocked for **15 minutes**. Wait, or ask an administrator to set a new password for you.
* After each sign-in the panel e-mails you when, from which address and from which browser and system your account
  signed in, and how (password, authenticator app, e-mail code or recovery code). If it was not you, change your
  password at once and use **Sign out others** on **My account**. You can turn this e-mail off there, unless the
  administrator keeps it on for everyone.
* You are signed out after **60 minutes without activity**, and after **12 hours** in any case. A super admin can
  change both times on the Settings page.
* **Sign out** (your name at the bottom of the sidebar, or **My account**) ends the session at once, on the server
  too: a copy of the session taken before cannot be used afterwards. Use it on shared computers.
* On a server reached through a tunnel, the panel's address can change when the server restarts. If the address you
  saved stops working, ask the administrator for the new one
  ([when an address changes](concepts/PUBLIC_ADDRESSES.md#6-when-an-address-changes)).

---

## 2. Projects

A **project** is one GitHub repository, as on Odoo.sh. Each branch of the repository can run as an instance, and
each branch has a **stage**: production, staging or development
([Stages](#stages-production-staging-and-development)). **Projects** is the first entry of the sidebar; **All
instances** still lists every instance of the server.

Instances that existed before projects joined their repository's project by themselves, with their names, stages,
links and backups unchanged. Instances of plain Odoo (without a repository) belong to no project: the Projects page
lists them under **Without a project**. Give one a repository (**Connect a repository** on its page) and it joins
that repository's project.

### The Projects page

One card per project: its name and repository, its Odoo version and edition, how many branches it has in each stage,
and how many of its instances run, are paused or are working. A **needs attention** mark counts instances with a
failed update, changes made by hand, or a branch deleted on GitHub. Click a card to open the project.

### Creating a project

**New project** (roles that may create projects: maintainers and up):

1. **Repository.** Either **Link a repository** that exists on GitHub (search the repositories the GitHub App can
   read, or paste its address), or **Create a new repository**: the panel creates a new, totally empty repository
   (private unless you switch it off, no README, no branch) in an organisation the GitHub App is installed on.
   Creating needs the app's **Administration: write** permission; when it is missing, the page says what to do on
   GitHub (an owner of the organisation accepts the change), then **Check again**. A personal account cannot get
   repositories from an app: create an empty one on GitHub and link it.
2. **Odoo.** The project's name (filled in with the repository's name; change it if you like, or clear it with the
   **x** to keep the repository's name), and the Odoo version and edition its new instances get. A branch can choose
   another Odoo version when you create its instance.
3. **Create project.** A linked repository's branches are listed at once; a new repository has none until you push
   the first branch.

### A project's page

**Branches** is the project's workspace. On the left, every branch of the repository (read from GitHub at most every
30 seconds; the refresh button next to **Filter branches** reads them now), grouped under **Production**, **Staging**
and **Development**. Each row shows the branch's name, its Odoo version (its instance's, else the branch's own, else
the project's) and a dot for its instance: green online, red
failing, amber working, a grey ring paused or switched off, a dashed ring no instance. **Filter branches** narrows the
list by name. On a phone the list folds into a button at the top that names the chosen branch.

Choose a branch (in the list, or after dragging one) and it opens at `/projects/<project>/branches/<branch>`. The
chosen branch is part of the project's header, under the project's details: its name in a pill with a button that
copies it, its stage and tags (**default** for the repository's default branch, the lock, **deleted on GitHub** when
it is gone) and its menu (**…**); its commands are on the header's right, under **Open on GitHub**. When you choose
another branch, its name, tags and command fade in where the previous ones were, and nothing else on the page moves.
On a phone the header shows the branch's name, then its tags, then its commands across the whole width. On the right
of the list come the branch's instance (its name, status and actions), then its **History** followed by its
instance's tabs (Overview, Activity, Code, Logs, Backups and the others, as on the instance's own page). A branch with
several instances has a list next to its name to choose the instance; a branch without one has **Create instance**
next to its menu and shows its latest commit and its history when it has one. While
an instance of the branch is being created (by you, or by the panel for a new branch), the branch shows its progress
instead, and a second one cannot be created by mistake. Its
settings appear once it has an instance: they describe how the running branch builds. The page opens on the
production branch, else the first branch.

**Commands.** On the right of the project's header, one tab per task and the command for this branch under it, with
a button that copies it (**Example:** marks a command to adapt), and a line under it saying what the command does
(point at it to read it whole when it is cut short). **Clone** downloads the branch with its submodules.
**Fork** makes a new branch from this one: type its name and the command follows. **Merge** merges another branch
into this one (production by default, staging for a production branch; choose another in the list). **SSH** opens a
shell in the instance's Odoo container through SSH to the server, and **SQL** opens its database in psql (both need
the right to open a shell or to see the instance's credentials; **Open the web shell** goes to its **Shell** tab).
**Submodule** shows how to add a repository of modules, with **Add submodule** for those who may update the
instance. **Delete** shows how to delete the branch on GitHub and has **Delete instance** for those who may delete
it. The tab you chose stays chosen while the browser tab is open.

**History** (the branch's first tab) lists, newest first and grouped by day, every build of the branch, as on Odoo.sh:
one card per commit an instance was built from, with the author, when, what started it (a new instance, a push,
**Get latest code**, a rebuild from production) and how long it took, the commit's subject and short hash (press it to
copy the whole hash; the arrow opens the commit on GitHub), and its status: **Test: Success**, **Warning** (Odoo's log
had warnings or errors while loading the modules; **Show the messages** lists them), **Failed** (with **Why it
failed**, below) or **Building**. Each instance has one live build, its newest successful one, with **Open Odoo** (its arrow offers
**Connect as…** and **Access details**, to those who may see the credentials); older builds are marked **Dropped**. For staging and development, the live build says
when the instance will be deleted. Stage changes (**Stage change: Development › Staging**, with what the new stage
does) and restores appear in the same timeline. **View steps** opens the build's job; the panel keeps the last 300 jobs,
so an older one says **Job no longer kept**. **Show older** reads further back; the panel keeps the last builds of each
branch (**Builds kept per branch**, 200 by default, in **Settings**, **Instances**, **Projects**).

**Why it failed.** A failed build (and a failed job, in the instance's **Activity** tab) shows the step that failed,
the reason in plain words and **Error details**: only the lines that explain the failure, never the whole technical
log. These are, depending on what broke, the Odoo error and the end of its traceback while installing or upgrading
modules, the git message (a submodule's repository not found, a missing access right), the failing tests with their
messages, or the Python package that could not be installed. Server paths, addresses and secrets are removed; the copy
button copies the lines. The full technical log stays on the server; your administrator can read it. When the panel
cannot read a repository (a new private submodule, or the main repository), the fix is right there for those who
manage the project: each repository with **Access OK** or **No access**, then the two ways to fix it, in this order:
give the panel's GitHub App access to that repository (recommended; the button opens the App's installation on
GitHub), or create a read-only deploy key for it and add it on GitHub. **Check again** reads the access again; once
every repository can be read, **Retry the update** runs the update again. The **Builds** tab shows the step and the
reason on each failed build; its card opens the branch's history.

**E-mail to the person who pushed.** When a build started by a push fails, the person who pushed receives an e-mail
with the project, the branch, the commit, what failed, the error details and what to do (for a repository the panel
cannot read: the same two ways, with a link to the branch). It is sent once per branch and commit, never to a GitHub
noreply address; the build then says whether it was sent. Those who manage the project turn it off with **E-mail the
pusher when a deployment fails** in the project's **Settings**.

**Changing a stage by dragging.** Drag a branch from one group onto another (with a mouse, or a long press on a touch
screen). The groups it can go to are outlined while you drag, the one under it is filled. Dropping it asks for the
same confirmation as **Move to** below; **Cancel** leaves it where it was. With the keyboard, move to a branch, press
Space to pick it up, the up and down arrows to choose the stage, Space to drop it, Escape to put it back. Only those
who may change the branch's stage can drag it (the project's access and the branch's lock apply).

**New branches.** The **+** next to **Staging** and **Development** explains how to add a branch: branches are created
on GitHub, by pushing one. Choose the branch to start from and a name, and copy the command
(`git checkout -b <new> origin/<from> && git push -u origin <new>`). The new branch appears in **Development** when the
branches are read again; drag it to **Staging** to make it a staging branch.

The menu (**…**) next to the branch's name has its actions:

| Action | What it does | Who |
| :--- | :--- | :--- |
| **Create instance** | A new instance of the branch, in the branch's stage, with the branch's Odoo version and the project's edition. **Odoo version** starts on the branch's own version, else the project's; choose another and the branch keeps it, so its later instances (made by hand, for new commits or for pull requests) get it too. **Name** is filled in with the name the panel would give (repository and branch); type another, or clear it with the **x**. Development: a fresh database, with Odoo's demo data unless you switch it off. Staging: a neutralised copy of the project's production instance (below); when you choose an Odoo version of another series than production's, production's data cannot go into it, a warning says so and the staging instance starts with an empty database. Production: a fresh database without demo data. | instances.create (staging also instances.restore) |
| **Logs**, **Backups** | Opens the instance's Odoo log, or its backups (with how many there are). | everyone (Logs: instances.logs) |
| **Move to** a stage | Changes the branch's stage and moves its instances with it, like dragging it. Making a branch production asks you to confirm what changes: its instances never pause, never expire, are backed up every day and get new code only through a backup and an automatic rollback. A project has one production branch unless the administrator allows more. Leaving production asks for a confirmation too. | maintainers and up |
| **Rebuild from production** | Staging only: replaces its database and files with a new neutralised copy of production, then takes the branch's latest code. Everything done in staging since its last rebuild is lost. | instances.restore |
| **Compare** | What the branch brings compared with another one (production's branch first): commits ahead and behind, the commits, how many files changed, the Odoo modules added, removed and with a new version (from each module's manifest), the migration scripts those versions run, and **Open on GitHub**. Swap the two branches with the arrows. | everyone |
| **Promotion check** | Staging only: the pre-promotion checklist (below), to read before merging the branch into production on GitHub. | everyone |
| **Delete instance** | Deletes the instance after a backup (a production instance asks you to type `production` too). The branch stays. | instances.delete |
| **Lock**, **Change the lock**, **Unlock** | Closes the branch to everyone but the people and teams the lock allows ([locked branches](#project-access-and-locked-branches)). | administrators |

**The pre-promotion checklist.** Moving a branch into production (by dragging it or with **Move to**) shows a
checklist first, each line green, amber or red with the reason: the branch's latest build and its tests;
production's newest backup (**Back up now** when it is older than the administrator's limit, 24 hours by default);
the modules that change between production and the branch; the migration scripts that will run; whether both run
the same Odoo version; locks; and the room left in the project, the plan and the server. Red lines stop the move.
If you manage the project and know why a line is red, tick **Promote anyway**: the move is made and the audit log
records it. **Compare with production** opens the full comparison. See
[Promoting to production](ADMIN_GUIDE.md#promoting-to-production).

What you may do in a branch also depends on the project's access: a branch hidden from you is not listed, and a
**Locked** tag marks a locked branch (its menu then offers only what the lock lets you do). **Restricted** marks a
branch seen only by its own grants.

For those who manage backups, the branch's menu shows how many backups its instance has (**3 backups**); the link
opens the instance's **Backups** tab.

**Builds** (the project's tab) shows every branch at once, under **Production**, **Staging** and **Development**: the
branch's name and status dot, **GitHub** (the branch on GitHub) and, for a staging branch you may rebuild,
**Rebuild**, then its last four builds side by side (scroll sideways for more): the commit's subject, its author, its
age and duration, a coloured band for its status (green success, amber warning, red failed, grey dropped, moving
stripes while building), and **Open Odoo** on the live build. Press a build to open the branch's history. **Filter
branches** narrows the rows by name.

**Backups** (administrators and super admins: those who manage backups) lists the backups of all the project's
branches, one card per branch with its stage, the newest backups first. Each backup has **Download**, **Restore**
(puts it back into the instance it was taken from, after a confirmation, as on the instance's page) and **Delete**.
The project's access applies: a branch hidden from you is not listed, and in a locked branch only what the lock lets
you do is offered. The **Backups** page in the sidebar has the backups of every project and instance.

When the server runs as many instances as it takes comfortably, a warning above the branches says so: many branches
running at once slow every instance down. Development instances pause when nobody uses them.

**Staging from production.** A staging instance runs on a copy of production's data that cannot reach the outside
world: no e-mail goes out (the **Mail** tab shows what it would have sent), scheduled actions are off, payment
providers and other services are in test mode or off, it has its own database identity and its own link, and its
administrator has the instance's own password (on its **Access** tab). By default a push to a staging branch
rebuilds it from a new copy of production. Creating or rebuilding a staging instance by hand needs the right to
restore production's instance too, so a production branch restricted to some people keeps its data from the others;
a push rebuilds staging only when everyone who may open the staging instance (its password, shell, backups or caught
e-mail) may also do so in production, and otherwise just brings the new code.

**What happens on GitHub by itself** (each project can change it on its **Settings** tab):

* **A new branch** gets a development instance, up to a few per project (3 by default). Beyond that, or when the
  project does not do it, use **Create instance**.
* **A push** does what the branch's **Behaviour upon new commits** says (below): by default it updates a development
  instance in place, and a staging instance is rebuilt from production first (or only takes the code, if the project
  says so).
* **A deleted branch:** its development instance is backed up and switched off by default (it can be started again,
  and its lifetime still ends it); the project can keep it or delete it instead. Staging is switched off at most and
  production is never touched.

**Build options and tests**, as on Odoo.sh. The project's **Settings** give every branch its defaults
(**Development branches defaults**), and each branch can change them for itself on its **Branch settings** tab, the
last of the branch's tabs (maintainers and up). The tab first shows the Odoo version the branch's instance runs;
moving a running instance to another version is an upgrade, on the instance's **Upgrade** tab
([Upgrading to a newer Odoo version](#upgrading-to-a-newer-odoo-version)). A branch without an instance has no
settings to show. Clear **Use the project's defaults** to give a branch new build
options of its own; next to each value a quiet label says where it comes from (**This branch**, **Project default**
or **Panel setting**), and the reset button goes back to the default. Production has no options: it always takes new
commits through the safe update.

* **Behaviour upon new commits:** **Update** (the new code in the same database, the changed modules upgraded),
  **New build** (development: a new database built from scratch, the old data is gone; staging: a new copy of
  production) or **Do nothing** (the commit is only listed in the branch's history, as **Commit received**, until
  you click **Get latest code**).
* **Module installation** for new development databases: **the repository's modules**, **all modules** (every module
  Odoo has, with the repository's; no tests) or **a list** of module names.
* **Test suite:** **off**, **the repository's modules** (the tests of its installed modules) or **custom tags**
  (Odoo's `--test-tags`, such as `/sale_custom,-slow`). Tests run before the instance opens; a failed test marks the
  build **failed** ("At least one test failed when loading the modules (module_x)") with the failing lines, and the
  instance still opens. Each build in the history says how its tests went: **Tests: 132 passed**, or how many failed
  and in which module. Staging runs them on a throw-away copy of its database, only when the project turns
  **Tests on staging** on.
* **Demo data** for new development databases.

When the GitHub App may write commit statuses, each build's result also shows next to its commit on GitHub (and in
pull requests) as `sudoo/development`, `sudoo/staging` or `sudoo/production`; **Details** opens the branch's history
here. The project's **Commit statuses** switches it off.

**Settings** (the project's tab, maintainers and up): the name, the Odoo version and edition of new instances, what
happens with new, pushed and deleted branches, the build options above, the lifetimes of new development and staging
instances, and
**Delete the project** (only without instances; the repository on GitHub stays). A value you do not set follows the
panel's setting (**Settings**, **Instances**, **Projects**); the reset button next to it goes back to it.

**Scheduled refresh from production** (in **Settings**): staging can be rebuilt from a new copy of production by
itself, **Daily** or **Weekly** (choose the day), at a time in the panel's time zone, for every staging branch or the
ones you tick. It does what **Rebuild from production** does, so whatever was done in staging since is lost. A
branch that is locked, busy or cannot be rebuilt is skipped, and its history says why; each refresh appears in the
branch's history. **Refresh now** runs it at once. The card shows the last and the next run. A notification channel
with the event **Staging refreshed from production** is told each time ([Status and notifications](#status-and-notifications)).

**Pull-request previews** (in **Settings**, off by default): each open pull request on GitHub gets a development
instance of its branch, and the panel writes one comment on the pull request with the preview's link, updated after
each build. When the pull request is closed or merged, the preview is deleted (without a backup). A branch that
already had an instance keeps it, and that instance is the preview. **Previews at most** limits how many a project
has at once (3 by default). **Previews of forks** stays off unless you trust the people who send pull requests from
forks: their code would run on your server. The card lists the pull requests with their previews and status. See
[Pull-request previews](ADMIN_GUIDE.md#pull-request-previews).

**Access** (the project's tab, administrators): who sees the project and at which level, per project and per branch,
and its locked branches: see [Project access and locked branches](#project-access-and-locked-branches).

### Project resources

A project's **Settings** tab has a **Resources** section, as Odoo.sh's workers and database size: one card per
stage (production, staging, development) with the values its instances get.

* **Workers** and **Cron workers**: steppers. 0 workers is Odoo's threaded mode (one process). About one worker for
  six people using Odoo at the same time. With workers, the Odoo container's memory and cores are computed from them
  when you do not set them ("Odoo container: 2.4 GB, 2 cores").
* **HTTP timeout** and **Scheduled actions timeout**: the seconds one request, or one scheduled action, may run.
* **More limits**: processor time per request, memory per worker, the Odoo and database containers' memory and
  cores, the database connections.
* Under each field: where an empty field's value comes from (the stage's default) and the most you may set (the
  administrator's maximum). The arrow next to a value you set brings back the default.
* The line at the bottom says how much memory the server reserves for all its instances, and how much after your
  change, while you type. A change the server cannot hold is refused before it is applied.
* **Apply** restarts the instances of that stage with the new values. Production asks first, is backed up, and gets
  its previous values back if Odoo does not answer with the new ones.

The **Storage** card lists the database and files of every instance of the project against its quota, production's
database size first.

A branch can differ from its project: on its **Branch settings** tab, **Resources of this branch**, click
**Override for this branch** and set only what differs; **Follow the project** removes the branch's own values.
Changing resources needs the **Project resources** permission (administrators by default).


### Status and notifications

**Status** (the project's tab, everyone who sees the project): whether production is up, as Odoo.sh's status page.

* The banner at the top says **All systems operational**, **Degraded** (something is slow or late, or a backup is
  missing) or **Down** (a production instance does not answer).
* One card per production instance, its backups (and off-site copies), and the server: its state now, its uptime over
  the past day, week and month, a bar per day for 30 days (hover or focus a bar for that day's uptime), and, for the
  instance, its response time per day.
* **Incidents**: each time something was not up, when it started, how long it lasted and what was seen.
* **Public status page** (maintainers and up): a read-only link you can give to your customers. It shows only names,
  states and uptime. **New link** makes the old one stop working.

**Notifications** (the project's tab, maintainers and up): where the project tells you what happened. **Add channel**,
choose Slack, Discord, Telegram, Microsoft Teams, Webhook or E-mail, paste the address (or the bot token and chat id,
or the addresses), and tick the events you want. Saved addresses and tokens are never shown again, only their last 4
characters; type a new one to replace it. A webhook's signing secret is shown once, when it is made: copy it then.
**Send test** sends a test at once; each channel says how its last delivery went.

---

## 3. The Instances page

This is the first page after signing in (**All instances** in the sidebar). The sidebar on the left leads to the
other pages:

| Sidebar | Pages | Shown to |
| :--- | :--- | :--- |
| Top | **Projects**, **All instances** (this page), **New instance**, **Help** | everyone (New instance: roles that may create instances) |
| **Operations** | **Backups**, **GitHub access**, **Audit log** | administrators and super admins; GitHub access also to maintainers |
| **Administration** | **Settings**, **Accounts**, **API tokens** | Settings: super admins; Accounts and API tokens: administrators and super admins |
| Bottom | your name and role (opens **My account**), the theme button | everyone |

On a small screen the sidebar opens with the menu button at the top left.

**Server figures** (the five cards at the top). Each card shows the figure now and, inside it, a small line of the
last hour:

| Card | What it shows |
| :--- | :--- |
| Processor | How busy the server's processor is. |
| Memory | Memory in use on the server. |
| Disk | Disk space in use on the server, and about how many more instances fit on the free disk. |
| Panel | Memory the panel itself uses (its backend, its web service and the public-link connectors), the most it used in the last hour, and its share of the processor. |
| Running | How many instances are running now, of about how many the server runs comfortably at once ([How many instances can my server run?](ADMIN_GUIDE.md#how-many-instances-can-my-server-run)). |

When the server's memory is nearly full, or more instances run than it takes comfortably, a warning above the
cards says so. **New instance** and **Start** show the same kind of warning before one more instance starts; they
still let you go ahead.

**Charts.** Below the cards, four charts show what the cards do not:

| Chart | What it shows |
| :--- | :--- |
| **Requests** | Requests per minute of all instances together over the last hour, with the errors (answers 500 to 599) as a second line. |
| **Instances** | A ring of the instances by state (**Online**, **Working**, **Paused**, **Switched off**), with the total in the middle. |
| **Response time** | How long Odoo takes to start answering, over the last hour: the slowest instance's 95th percentile (95% of its requests were faster) and the average. |
| **By instance** | Bars of the instances that use the most now, each with its value (inside the bar when it fits, otherwise just after it). Switch between **CPU**, **Memory** and **Disk**. Click a bar to open that instance. In Arabic the chart reads from right to left. |

**The list of instances.** Use **Search** to find an instance by name, the filters **All**, **Online**,
**Paused** and **Working** (each with its count), and the stage menu (**All stages**, **Production**, **Staging**,
**Development**). Production instances come first. Each instance has a card with:

* its name and status ([statuses](#statuses)),
* its stage: **Production** stands out, **Staging** and **Development** stay quiet
  ([Stages](#stages-production-staging-and-development)),
* its Odoo version, edition (Community or Enterprise) and branch,
* the last commit, or, while a job runs, that job with its progress,
* processor, memory and requests per minute,
* **Open**, which opens Odoo in a new tab.

Click the card to open the instance's page.

The page updates by itself: new instances, status changes and job progress appear without reloading. When the
connection to the server is lost for a moment, a small **Reconnecting** notice shows until it is back.

---

## 4. An instance's page

### The header

At the top: the instance's name, stage and status, its Odoo version, edition, branch and link. While a job runs, a line
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
| **Update submodules** | Moves every submodule to the newest commit of its branch and restarts ([section 7](#7-submodules)). |
| **Upgrade modules** | Like **Upgrade** in Odoo's Apps menu. Enter technical names separated by commas, or `all`. |
| **Switch branch** | Moves the instance to another branch of the same repository, upgrades its modules and restarts. The database stays. |
| **Update Odoo and PostgreSQL images** | Downloads the newest build of the instance's Odoo version and PostgreSQL (same versions) and restarts with them. Until then the instance keeps the exact images it was created with. |
| **Pause** | Stops it to free memory now. It starts again on the next visit. A production instance asks first. |
| **Switch off** | Stops it completely. Visitors see that it is switched off, and it does not start until someone clicks **Start**. A production instance asks first. |
| **Delete** | Removes Odoo, its database and its files, after a backup. Type the instance's name to confirm (a production instance: also the word `production`). If the backup fails, nothing is removed; someone who may also manage backups can turn on **Delete even if the backup fails** to remove the instance anyway (the audit log records it). |

**Back up now** and **Restore the latest** are on the **Backups** card of the **Overview** tab
([The Backups card](#the-backups-card)).

**Changed outside the panel.** A red banner with this title means someone changed the instance's containers or
files on the server directly, not through the panel. It lists what differs. Usually the panel puts its own
configuration back by itself within a minute (you see a **Restore the panel's configuration** job in Activity) and
the banner goes; otherwise the banner has a button for it. Tell your administrator either way.

Each button and menu item is shown only to roles that may use it. While a job runs on the instance, the buttons
wait: one job per instance at a time.

### Statuses

| Status | Meaning | What to do |
| :--- | :--- | :--- |
| **Online** | Odoo answers. | Nothing. |
| **Starting** | The container runs but Odoo is not answering yet. | Wait up to a minute. If it stays, open the **Logs** tab. |
| **Working** | A job (update, restart, backup...) is running. Other actions wait their turn. | Wait; click the job line to follow it. |
| **Paused** | Stopped to save memory because nobody used it for some hours (6 by default). | Nothing: it starts by itself when someone opens its link, or click **Start**. |
| **Switched off** | Stopped on purpose. It does **not** start when someone opens it. | Someone allowed to do so clicks **Start**. |
| **Stopped** | Odoo stopped unexpectedly, often for lack of memory. | **Start**. If it happens again, check the **Logs** tab and pause other instances. |
| **Checking** | The panel has not measured the instance yet, for example just after the panel started. | Wait a few seconds. |

### Tabs

| Tab | Contents |
| :--- | :--- |
| **Overview** | Four cards with the processor, memory, requests per minute and response time of this instance, each with a small line of the last hour; one live chart with a **CPU / Memory / Requests / Response** switch; the **Backups** card; details such as the database, its size and how long it has been running. |
| **Activity** | Every job on this instance, newest first. Click one to open a panel with its steps (the running one with its progress, such as "Downloading Odoo 19.0: 2 of 5 parts"), notes and, if it failed, **Why it failed**: the step, the reason and the error details. It follows live while the job runs. |
| **Code** | The repository and branch, the recent commits, and the submodules, with **Add submodule** and **Remove** ([section 7](#7-submodules)). |
| **Logs** | Odoo's log as it is written, and the logs of the instance's latest creation, update and module upgrade ([The Logs tab](#the-logs-tab)). Lines with `ERROR` or `Traceback` usually explain a problem. |
| **Backups** | The **Backups** card on its own ([The Backups card](#the-backups-card)): the instance's backups, **Back up now** and the restores. |
| **Domains** | The instance's own domains, such as `erp.client.com`: their state, the DNS record to create, the certificate and its expiry ([Custom domains](#custom-domains)). Everyone sees them; administrators add and remove them. |
| **Settings** | The stage ([Stages](#stages-production-staging-and-development)), the automation switches and the lifetime ([section 6](#6-keeping-the-code-up-to-date)), saved at once; and **Resources** ([Resources](#resources)). |
| **Access** | The Odoo sign-in (**Connect as**, next to **Open Odoo** at the top of the page, opens Odoo signed in as any of its users without their password: [Connect as](#connect-as)): address, login, password, and the master password (for Odoo's database manager). Click **Show the passwords**; each viewing is recorded in the audit log. |
| **Mail** | Staging and development only: the e-mails the instance sent, caught before they reached anyone ([The Mail tab](#the-mail-tab)). Developers and up. |
| **Shell** | A terminal in the browser, inside the instance's own containers: a command line in the Odoo container, `odoo shell`, or `psql` on its database ([The Shell tab](#the-shell-tab)). Only for the roles allowed to. |
| **Tools** | The code profiler: record what Odoo spends its time on for a few minutes, then read it as a flame graph ([The Tools tab](#the-tools-tab)). Maintainers and up, by default. |
| **Upgrade** | Administrators: move the database to a newer Odoo version: the pre-check, a test on a new staging instance, then the upgrade itself with its backup and rollback ([Upgrading to a newer Odoo version](#upgrading-to-a-newer-odoo-version)). |

### The Logs tab

The **Logs** tab follows a log live, as Odoo.sh does.

* **Log.** `odoo.log` is what Odoo writes (its server log). The technical logs of the panel's jobs (creation,
  updates, module upgrades) are server logs and are not shown here: a job's steps, progress and **Why it failed** are
  in the **Activity** tab, and your administrator can read the full technical log on the server.
* **Follow and Pause.** New lines appear as they are written. **Pause** keeps them aside: a pill shows how many new
  lines arrived, and clicking it (or **Follow**) shows them. Scrolling up stops the scrolling only; **Jump to the
  latest lines** goes back to the end.
* **Search.** Type a word: only the lines that contain it are shown (any case), and the word is highlighted. Use the
  arrows, or Enter and Shift+Enter, to step through the matches.
* **Levels.** Errors and CRITICAL lines are red, warnings orange; the request lines of `werkzeug` are dimmed so the
  rest stands out. The level buttons show only one level.
* **Wrap** breaks long lines (on by default); off, the log scrolls sideways.
* **Download** saves the log as a text file: its last lines (100,000 unless an administrator changed
  `LOG_DOWNLOAD_MAX_LINES`), with the search applied.

When Odoo is stopped, the tab shows the last lines it wrote and follows again once it starts.

### Connect as

**Connect as**, next to **Open Odoo** at the top of the instance's page, opens Odoo signed in as one of its users, without knowing or changing their
password, for example to see what a salesperson sees. Click the arrow next to **Open Odoo**, then **Connect as…**,
choose the user, and click **Get the sign-in link**, then **Open Odoo as ...**. The link works once, for one minute.

* Only the roles that may see the Odoo passwords can use it (administrators and up).
* On a production instance you type `production` first: you act as that user on live data.
* Giving the link and opening it are both written to the audit log: who, which instance, which Odoo user.
* Odoo opens an ordinary session for the user: it ends when you sign out of Odoo, when the user's password changes,
  or after Odoo's usual session lifetime. The instance must be running.

### Resources

The **Settings** tab ends with **Resources**: how much of the server this instance may use, and how it is treated
when the server is busy.

* **Priority.** The instance's stage decides it: **Production, first priority under load**, then staging, then
  development. When the server's processor or memory is fully used, production gets them first and development
  gives them up first. On a quiet server this changes nothing: every instance gets what it asks for.
* **Usage.** The processor and memory this instance uses now, against its limits ("no limit" by default).
* **Storage.** The database and files of the instance, measured every hour, against its stage's soft quota. Near
  the quota the bar turns amber, over it red, and the administrators are alerted; nothing is stopped.
* **Limits.** By default there are none. An administrator can set, for this instance only, the Odoo workers and
  cron workers, the memory per worker, the time a request may take (the HTTP timeout), the scheduled actions
  timeout, the database connections, and the processor and memory of its Odoo and database containers. An empty
  field follows what the stage, the project or the branch sets, shown under it. Warnings appear when the values
  look risky for this server; they never stop you.
* **Apply** restarts Odoo with the new values (a production instance asks first and is backed up before). A notice
  says when the instance runs with older values, for example after a change of stage, until it is restarted.

Everyone who can open the tab sees the resources; only administrators change them.

### The Backups card

The **Backups** card holds the instance's backups. It is on the **Overview** tab and has a tab of its own,
**Backups** (the address ends in `/backups`, which the project's page links to).

| Part | What it does |
| :--- | :--- |
| **Back up now** | Saves the database and the files as an Odoo backup now, with an optional comment that says why ("before the price import"). A running instance keeps running; a paused one stays paused. |
| **Import database** | Replaces the instance's database with a dump from elsewhere ([Importing a database](#importing-a-database)). |
| **Restore the latest** | Puts back the newest backup taken by hand, daily or before a deletion. Greyed out when the instance has none. |
| **Restore another backup…** | Opens a list of every backup on the server, grouped by instance, with its Odoo version and date. Pick one and confirm to put it into this instance, even a backup of another instance. Backups of another Odoo version, or Enterprise backups on a Community instance, are greyed out with the reason. Shown to roles that may both restore and manage backups (administrators and super admins). |
| **Latest backup** | The date and time of that backup, its kind, its size and how long ago it was taken. |
| The list | Every backup of the instance, newest first, with when it was taken, its kind, the revision (the commit the code was at, linked to the repository), the comment and its size. **Restore** puts back that backup; **Download** (the arrow) saves the zip, for roles that may manage backups. |

The kinds of backup are explained in [section 11](#11-backups-and-audit-log).

**Restoring.** Click **Restore the latest**, or **Restore** on a line of the list. The dialog shows the backup's date,
kind and size, and says that the database and its files go back to how they were then: changes made since are
replaced. Click **Restore** to confirm. The job:

1. saves the current state as a **Before a restore** backup, so the restore can be undone,
2. stops Odoo,
3. replaces the database and the files with those of the backup, and sets the instance's current link in the
   database again,
4. starts Odoo.

It takes about a minute or two. The instance is running afterwards, even if it was paused before.

**A backup of another instance.** With **Restore another backup…** you can put any instance's backup of the same Odoo
version into this one, for example to give a test instance a copy of the live data. The backup's database is loaded
under this instance's database name, and its files go with it. Because the data comes from another instance, the
job then neutralises it, the way Odoo.sh does for copies: outgoing mail servers and scheduled actions are switched
off (Odoo 16 and later use Odoo's own `neutralize` command, which also covers payment providers and other
integrations of installed apps), and the copy gets its own database identity. The job's notes say so. Switch mail
servers and scheduled actions back on in Odoo when you want them. The copied administrator (the first user, usually
`admin`) gets this instance's admin password, so the **Access** tab still works. The other instance's modules come
with its data: if this instance's repository does not have them, Odoo skips them (their screens are missing) until
you add them. A backup of the same instance is put back as it
was, without neutralising.

**Rollback.** After a restore, a banner at the top of the instance's page says when the instance was restored and
offers **Rollback to before the restore** until a given time (24 hours after the restore unless a super admin changed
`ROLLBACK_HOURS`). Click it and confirm to go back to the state just before the restore. The rollback saves the
current state first as a **Before a rollback** backup, kept as long, and never replaces the state it goes back to.
Once you have rolled back, the banner goes; to go back to that state again, restore the **Before a restore**
backup from the Backups card. After that time the **Before a restore** backup is removed and the banner goes. A second restore within that time
replaces it: Rollback always goes back to just before the last restore.

**Importing a database.** **Import database** puts a database from elsewhere into this instance: a backup
downloaded from Odoo.sh or from Odoo's own database manager (a `.zip` with `dump.sql` and the filestore), a
`pg_dump` file (`.dump`), or a plain SQL file (`.sql` or `.sql.gz`). Drop the file on the dialog or choose it, then
click **Upload and import**.

* The file goes up in pieces. If the connection drops, the upload continues by itself; after closing the page,
  choose the same file again and it continues where it stopped. The largest file is 20 GB unless an administrator
  changed `IMPORT_MAX_GB`.
* The dump must be of the instance's Odoo version (17.0 into 17.0). A dump of another version is refused with the
  reason: import it into an instance of its own version, then upgrade that instance.
* A zip whose paths would leave the archive is refused.
* **Neutralise the imported data** is on by default (off in production): outgoing e-mail, scheduled actions and
  payment providers are switched off, as for a copy. Untick it only when the imported data should act as the original.
* Production asks you to type `production`. On every instance the current state is backed up first and offered as
  **Rollback** for a while, as after a restore.
* Users keep the passwords they have in the dump. Use **Connect as** to sign in as any of them.

A `.dump` or `.sql` file has no filestore: attachments stored as files are not part of it.

### Managed address

When your Sudoo.sh account offers managed subdomains, every instance also gets a name under Sudoo.sh's domain, made
of its branch, its stage and your company, for example `main.production.acme.sudoo.cloud`. The **Overview** shows it
under **Managed address**, with "its certificate is on the way" for the first minutes; once it is served it is the
instance's address (unless the instance has a primary custom domain). It follows the instance: a new branch or stage
gives it the new name, and deleting the instance releases it. Administrators find its state in **Settings, Custom
domains** ([Administrator Guide](ADMIN_GUIDE.md#managed-subdomains)).

### Custom domains

An instance, usually a production one, can answer on your client's own domain, such as `erp.client.com`, over
https. The **Domains** tab shows its domains; administrators add and remove them. Your administrator first turns
custom domains on for the server ([Administrator Guide](ADMIN_GUIDE.md#19-custom-domains-and-certificates)); until
then the tab says so, and why (a server reached only through the default public links cannot carry your own
domain without a proxy in front of it).

To add one:

1. On the instance's page, open **Domains** and click **Add domain**.
2. Type the domain: only the name, such as `erp.client.com` (no `https://`, no path). On a production instance the
   button says **Add to production**: adding confirms that its address may change.
3. The panel shows the DNS record to create at the domain's DNS provider, for example a **CNAME** record named
   `erp.client.com` with the value the panel shows. For a bare domain (`client.com`) it shows **A** (and **AAAA**)
   records instead. Copy each value with its copy button. Behind Cloudflare, set the record to **DNS only** (grey
   cloud).
4. Wait. The panel checks DNS on its own, every minute at first and then less often; DNS changes usually show
   within minutes, sometimes hours. **Check now** checks at once.

What the states mean:

| State | Meaning |
| :--- | :--- |
| **Waiting for DNS** | The domain does not point at this server yet; the reason says what was found. Create or fix the record shown. |
| **Getting a certificate** | DNS is right; the certificate is being issued, usually in under a minute. |
| **Active** | The instance answers at `https://<domain>` with a valid certificate (the lock next to the name opens it). Visits over http are sent to https. |
| **Renewing** | The certificate is being renewed (30 days before it expires, on its own). The current one keeps working. |
| **Expires soon** | The certificate expires within two weeks and could not be renewed yet: the reason says why. Administrators get an alert. |
| **Failed** | The certificate could not be issued; the reason says why (often port 80 closed, or the record points elsewhere). The panel tries again later, waiting longer each time; fix the cause and click **Check now**. |

The first domain you add is the **primary** one. Once it is active it becomes the instance's address: **Open Odoo**,
the address in the header, and the links in Odoo's e-mails and portal (`web.base.url`) use it. The instance's own
link (shown as **Default address**) keeps working too. To make another domain primary, open its **⋯** menu and
choose **Make primary**; **Remove** in the same menu removes a domain and deletes its certificate.

When an instance moves to another server (several servers), its custom domains go with it, primary one included,
with their certificates: the new server serves them at once, the old one stops. Their DNS still points at the old
server, so each one shows "moved here from <server>" with the record to create now; change it at the domain's DNS
provider. The panel checks on its own (and with **Check now**) and the note goes once the domain points at the new
server.

---

### The Mail tab

Staging and development instances never send real e-mail. Everything Odoo sends there (quotations, invoices,
password resets, mass mailings) is caught by the panel's mail catcher and shown on the instance's **Mail** tab,
like the mail catcher of Odoo.sh. Production instances send their e-mail as usual and have no Mail tab.

* The list shows the newest e-mails first: subject, sender, recipients, when, size and a paper clip for
  attachments. **Search** looks in the subject, the addresses and the start of the text.
* Click an e-mail to read it: **HTML** (as a mail program shows it; scripts, forms and remote images are blocked),
  **Text**, **Headers** (every header as received), and its attachments to download. **Download .eml** saves the
  whole message, to open in a mail program.
* Administrators can **Delete** one e-mail or **Delete all**, and **Release** one: send a copy to an address they
  type, to check it in a real mailbox. The copy comes from the panel's own address, with the original sender as
  Reply-To. Releasing can be switched off by the administrator, and every release is recorded in the audit log.

Each instance shows only its own e-mails. They are kept 7 days and up to 500 per instance by default (the oldest go
first). When an instance moves out of production, Odoo sends to the catcher after its next **Restart**; until then
its mail servers are switched off, so nothing goes out.

### The Shell tab

The **Shell** tab opens a terminal in your browser, connected to one of the instance's own containers, like the
shell of Odoo.sh. It is for people who know the command line: what you type runs at once, on the instance itself.

1. Choose what to open:
   - **Terminal**: a command line (bash) in the instance's Odoo container, as its own user `odoo`, in the folder of
     the instance's code (read-only), shown as `~`. `odoo --version`, `ls`, `python3` and the like work, and so do
     the [useful commands](#the-terminals-welcome-and-useful-commands) below.
   - **odoo shell**: Odoo's Python shell, already connected to the instance's database (`env`, `self`).
   - **psql**: SQL on the instance's database, in its database container. Only super admins and administrators
     see it.
   - **As root** (super admins, only when an administrator switched root shells on): the terminal runs as root
     inside the container. The container keeps its limits: root there is not root on the server.
2. Click **Open shell**. On a **production** instance, the panel asks you to confirm every time.
3. The bar above the terminal says **Connected to** *instance*, with the instance's stage in its colour:
   **production in red**, **staging in amber**, **development in green**. **Recorded** means that what you type and
   see is recorded and super admins can play it back (by default: production instances and root shells).

#### The Terminal's welcome and useful commands

When the **Terminal** opens, it greets you before the prompt:

- the Sudoo.sh banner (left out when the terminal is narrower than it, about 52 columns);
- which instance you are in and its stage ("You are connected to the **staging** instance **shop-staging**"), with
  its Odoo version and edition, project and branch;
- on **production**: "This is production: changes affect live users."; on staging and development: e-mail sent
  from the instance goes to the mail catcher;
- the **useful commands** that work in this terminal (`sudoo-help` shows them again):

| Command | What it does |
| :--- | :--- |
| `odoo-shell` | Odoo's Python shell on the instance's database, with its configuration. |
| `odoo-update <modules>` | Updates the modules (commas or spaces between them) on the instance's database, then stops. |
| `odoo-install <modules>` | Installs the modules, then stops. |
| `psql` | SQL on the instance's database, already signed in. |
| `cd` | Back to the code (`~`) from anywhere. |
| `sudoo-help` | Shows the list again. |

`odoo-update` and `odoo-install` run a second Odoo next to the one serving visitors, without a web server or
scheduled actions, and say whether it went well; the running Odoo picks the change up. On a production instance they
first ask you to type the instance's name. In a root terminal the Odoo commands are not offered (root cannot read the
instance's configuration there): open the Terminal as `odoo` for them. Logs, restarts and caught e-mail stay in the
panel's own tabs.

The prompt is `<instance> [<stage>/<version>]:<folder>$`, in bold and in the stage's colour, for example
`shop-prod [production/18.0]:~/sale_custom$`. The instance's code is `~`; the container's own name, the server's
paths, addresses and ports are never shown, and `hostname` answers with the instance's name.

Your own `~/.bashrc` in the container (if you keep one in Odoo's data folder) is read first, then the panel's welcome
and prompt, so your aliases and history keep working. Set `NO_COLOR=1` (in that `~/.bashrc`) for a terminal without
colours. Nothing in the welcome reaches the network: it shows at once.

Using it:

| What | How |
| :--- | :--- |
| Copy | Select text, then the **Copy** button, Ctrl+Shift+C, or ⌘C on a Mac. Ctrl+C stays an interrupt for the program running. |
| Paste | The **Paste** button, Ctrl+Shift+V, or ⌘V on a Mac. |
| Clear the screen | The eraser button (or `clear`). |
| Full screen | The full-screen button; Esc leaves it. |
| Close | `exit`, or the **×** button. Leaving the page closes the shell too. |
| Reconnect | After a shell closed: **Reconnect** opens a new one (the old one's programs were ended). |

A shell closes by itself after a while without typing (15 minutes by default; a notice comes a minute before), and
after its longest time (60 minutes by default). When it closes, **every program started in it ends**, also those
started in the background: use the panel's own buttons (or a job) for long work, not a shell. How many shells one
person and one instance may have open at once is limited too (2 and 3 by default).

Good to know:

- The instance must be running: start it first.
- A shell reaches only that instance's containers, never the server or the panel.
- Every shell is in the audit log: who opened it, where, for how long, and why it closed.
- The terminal always reads left to right, also when the panel is in Arabic.
- On a phone the panel suggests using a computer; **Open anyway** opens the terminal to read it.

Super admins also see **Shells in this instance** under the terminal: who opened one, when, for how long and how it
ended. **End** closes a shell someone has open; **Play** shows a recording, and **Download (.cast)** saves it in the
asciinema format.

### The Tools tab

**Tools** records what the instance's Odoo workers spend their time on, as on Odoo.sh, and shows it as a flame
graph. It is there for super admins, administrators and maintainers (the administrator can change who, see
[Admin guide](ADMIN_GUIDE.md#28-code-profiler)).

1. Choose a **Duration** (1 to 5 minutes by default) and press **Start profiler**. On a production instance you
   confirm it first, every time. The first recording on a server can take a minute to prepare.
2. Use the instance while it records, or repeat the slow action. A countdown shows how long is left; **Stop now**
   ends it early and keeps what was recorded.
3. The recording opens by itself when it is saved. Each bar of the flame graph is a function; the wider it is, the
   more time was spent in it and in what it called. The colours tell Odoo's own code, add-ons, the database, Python
   and the rest apart. Hover a bar for its file, line and share of the time; click it to zoom in, click a caller
   above it (or **Show everything**) to zoom out; choose one thread instead of all; type in **Highlight functions**
   to see how much time a function takes. **Where the time goes** lists the functions with the most time in their
   own code.

The last 5 recordings are kept. **Download** saves one as a speedscope file (open it at speedscope.app);
**Delete** removes it. For one slow request in detail (each SQL query and call), use Odoo's own profiler, which the
tab links to.

## 5. Creating an instance

Click **New instance** in the sidebar (roles that may create instances). Under **Code**, choose first:

* **From a repository**: a branch of your GitHub repository, with its modules (the steps below).
* **Plain Odoo (no repository)**: the official Odoo only, Community or Enterprise, any version offered. Only
  **Name** is asked (optional: empty gives `odoo-<version>-<edition>-<4 random characters>`, for example
  `odoo-18-0-community-3f9a`, shown in the hint), then the version, edition and options; go on with step 6.
  Such an instance has no code to update: **Get latest code**, **Switch branch**, **Update submodules**, the
  **Code** tab and the automatic update settings do not appear. Everything else works the same: pausing and
  starting, backups and restore, lifetime, its link, figures and the integrity checks. To add code later, open
  the instance, click **⋯** then **Connect a repository…**, pick the repository and branch, and choose whether to
  install its modules. The database and files stay; the instance restarts with the code and from then on works
  like one created from that repository.

1. **GitHub repository**: when the GitHub App is connected, type part of the repository's name and pick it from the
   list. A repository missing from the list is not among those the app may read: the link under the list
   (**Choose which repositories the app can read**) opens the installation's page on GitHub to add it. Otherwise paste the repository's address: on GitHub, open the repository, click the green **Code** button
   and copy the address, for example `https://github.com/acme-co/sales`.
2. The panel checks the repository while you type. The hint under the field says how many branches it found, or
   what is wrong.
3. **Branch**: pick the branch. The instance only ever takes code from this branch. When the branch is named like a
   version (`19.0`), the Odoo version follows it.
4. **Name**: filled in with the name the panel gives from the repository and the branch (it follows them until you
   type in the field). Type another, or clear it with the **x** to let the panel name it. Once you type, the hint
   shows the final name and the database name, for example "Will be **sales-main** · database `sales_main`".
5. Below the name, the **access report** shows one line for the repository and one for every submodule, each with
   a mark: green when the server can read it, red with the reason when it cannot. The reason says what applies, with
   a button to fix it: the GitHub App belongs to another account, is not installed on the repository's owner, or
   may not read this repository yet (**Choose repositories**). Without a GitHub App, add a deploy key under **GitHub
   access** ([section 8](#8-github-access-private-repositories)); the report is checked again when you come back.
6. **Version**: the Odoo versions offered on this server.
7. **Edition**: **Community** (Odoo's free edition) or **Enterprise** (Enterprise apps and interface). Enterprise
   can be chosen only for the versions whose Enterprise add-ons are on the server.
8. **Stage**: **Production**, **Staging** or **Development** (the default). The stage sets the options below to
   its own defaults; see [Stages](#stages-production-staging-and-development). When the server already has as many
   production instances as it allows, a warning says so and **Production** cannot be created.
9. **Options**:

   | Option | Effect |
   | :--- | :--- |
   | **Install the repository's modules** | Installs the repository's own modules (not those of submodules) after the database is created. |
   | **Load demo data** | Sample customers, products and orders, useful for presentations. |
   | **Get new code automatically** | New commits on the branch are applied by themselves ([section 6](#6-keeping-the-code-up-to-date)). |
   | **Pause when nobody uses it** | The instance pauses after some hours without visits, to free memory. |
   | **Lifetime** | Deleted that many days after creation (30 by default, 90 for staging), with a backup first; or **Keep until deleted by hand**. Not shown for production. |

10. **Start from a backup** (optional; administrators and super admins): switch it on and pick a backup from the
   list, grouped by instance. Only backups of the chosen Odoo version (and, for Enterprise backups, an Enterprise
   instance) are listed. The new instance starts with that backup's data and files instead of an empty database,
   loaded under its own database name. A backup of another instance is neutralised: no e-mail goes out and no
   scheduled actions run until you switch them back on in Odoo. **Install the repository's modules** then installs
   only the modules the backup does not have yet; **Load demo data** does not apply. The backup's administrator
   (the first user, usually `admin`) gets the instance's admin password, so the **Access** tab works for it.
11. Click **Create instance**.

If the server has a limit on the number of instances, or a waiting time between two new instances, a warning above
the form says so before you start, and the numbers under the button show how many exist.

The new instance's page opens at once with the status **Creating** (**Waiting to start** while another job on the
same name finishes) and shows each step as it happens, with its progress: downloading the
Odoo image (the first time for a version takes a few minutes; the log says how far the download is), preparing,
downloading the code, starting the database, creating the Odoo database, installing modules, creating the public
link, starting Odoo. A new instance usually takes **2 to 4 minutes**. You can leave the page: the work continues,
the instance is listed with the status **Creating**, and the job is under **Activity**.

When it is ready, the page becomes the instance's own page: the link is in the header and the login is on the
**Access** tab.

If a step fails, the page says **Not created** and explains what went wrong in plain words, with the step where it
stopped, and **Why it failed**. Everything created so far is removed, so you can simply try again (**Try again**).

### Stages: production, staging and development

Every instance has a stage, shown as a badge on its card and its page. It decides what the panel does with the
instance on its own:

| | Development | Staging | Production |
| :--- | :--- | :--- | :--- |
| What it is for | Day-to-day work on a branch | Rehearsing a release before it goes live | The live database your users work in |
| Pauses when nobody uses it | Yes, unless switched off | Yes, unless switched off | Never |
| Deleted at the end of its lifetime | Yes (30 days by default) | Yes (90 days by default) | Never: kept until someone deletes it |
| Daily backup | After a day it was used | After a day it was used (the administrator can turn it off for staging) | Every day, used or not |
| Updates | Backed up first and undone if they fail, unless the administrator turned that off | The same | Always backed up first and undone if they fail |
| New code automatically | As chosen | As chosen | Off by default: **Get latest code** when you are ready, or switch it on |

Instances created before stages existed are **Development** instances and work exactly as before.

**Production asks before it acts.** Pausing or switching off a production instance asks you to confirm. Restoring a
backup into it or rolling it back says it is the production instance (the state before is always backed up first).
Deleting it needs its name **and** the word `production`.

**How many production instances.** The server allows one production instance by default; the administrator can
change the number (**Settings**, **Instances**, **Stages**). When the limit is reached, **New instance** says so
and an instance cannot be moved to production until another one leaves it.

**Changing the stage.** Open the instance, tab **Settings**, and choose another **Stage** (roles that may change
automation: maintainers, administrators, super admins). Moving to **Production** switches off the automatic pause
and makes its lifetime "keep". Moving out of production asks you to confirm and needs the right to delete instances
(it can then expire); its settings then stay as they are until you change them. The audit log records every change of stage.

---

## 6. Keeping the code up to date

**Automatically.** When **Get new code automatically** is on, the panel checks GitHub for new commits on the
instance's branch (every 2 minutes by default, or at once when GitHub tells the panel about a push through the
GitHub App or a webhook). When there
are new commits, it downloads them, upgrades the modules whose files changed and restarts Odoo. The job appears in
**Activity** as "Get latest code", started automatically.

**By hand.** Click **Get latest code**. Use it right after a developer says "I pushed".

**When an update fails.** Before an update changes the code, the panel backs up the instance's database and files
(a backup "Before an update" in its list). If anything after that fails (the code cannot be downloaded cleanly, a
module upgrade fails, or Odoo does not come back after the restart), the panel puts the instance back by itself: the
previous code, the database from that backup when the upgrade may have changed it, and Odoo started again. The job
fails with a sentence like "The update to 9b1e0c4 failed at upgrading modules; the instance was put back to
4f2a9c1." and the reason, and the instance's page shows it until an update works. Fix the code and push again, or
click **Get latest code** to try the same commit again; automatic updates do not retry it on their own. If putting
the instance back fails too, the message says so and names the backup to restore (Overview tab, **Restore**). A
paused instance stays paused and is backed up only when modules are upgraded. Your administrator can switch this off
(`SAFE_UPDATES`).

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

Changes on this tab are saved at once. A lifetime that ends sooner than the current one (also any number of days
instead of **Keep until deleted by hand**) deletes the instance sooner, so only roles that may delete instances can
choose one; a maintainer can make the lifetime longer, not shorter.

### Upgrading to a newer Odoo version

An instance's database can be moved to the next Odoo version, the way Odoo.sh does it: you check, you test on a
copy, then you upgrade production. Open the instance, tab **Upgrade**, which offers **Upgrade to Odoo <next>**
(administrators and super admins).

1. **Check.** **Run the pre-check** reads the modules installed in the database and, for the modules of the
   instance's repository, looks for them in the **upgrade branch**: the branch named after the new version, for
   example `19.0`. Each module is **Ready** (its manifest version starts with `19.0.`), **Not ported** (another
   version, such as `18.0.1.2.0`), **No version series**, **Not installable** or **Missing**. Nothing is changed. If
   the repository has no upgrade branch, the test runs on plain Odoo with standard modules only, so your own modules
   are missing from it.
2. **Test.** **Test the upgrade** backs up the instance, upgrades that copy and puts it into a **new staging
   instance** on the new version (with the upgrade branch's code when there is one). The copy is neutralised like
   every staging copy: no e-mail goes out (it goes to the Mail tab), no scheduled actions run. Open it and test your
   processes. **Run the test again** repeats everything from a fresh backup. When the project's option **A push to
   the upgrade branch runs the test again** is on, a push to the upgrade branch does it for you (with OpenUpgrade;
   with Odoo's upgrade service a push only brings the new code, because the subscription code is never kept).
3. **Upgrade production.** When the test went well, **Upgrade production** asks you to confirm, then: Odoo is put
   into maintenance (visitors see an **Upgrading** page), a final backup is taken, the same upgrade runs on the
   latest data, the instance switches to the new version and the upgrade branch, and Odoo starts. If Odoo does not
   come up, everything is put back from that backup at once.
4. **After the upgrade.** For a while (60 minutes by default) **Cancel the upgrade** puts the instance back on the
   backup taken just before it; the panel also does that by itself if Odoo stops answering. Anything changed in
   Odoo after the upgrade is lost by cancelling (the upgraded state is kept as a backup, kind **Before a rollback**).
   **Keep the upgrade** ends that time early. Delete the test instance when you no longer need it.

**How the database is upgraded.**

* **Enterprise databases** use Odoo S.A.'s official upgrade service, under your Odoo Enterprise subscription and
  Odoo's terms (the page links to them, and you tick that you accept them each time). You type the database's
  **Enterprise subscription code**; it is used for that one job, never stored and never written to a log. The
  database's own code is used when it has one, as Odoo's script decides. The upgrade takes the time Odoo's platform
  needs, often an hour or more.
* **Community databases** use OpenUpgrade, the Odoo Community Association's open-source upgrade project. It goes one
  version at a time, and its coverage varies by module and version: a module it does not cover stops the upgrade.

**What no method does for you.** Your own modules must be ported to the new version by developers, in the upgrade
branch. The upgrade moves your data; the code that runs on it is yours.

---

## 7. Submodules

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
3. **Folder** (filled in with the repository's name; type another, for example `oca/web`) and **Branch**. For OCA repositories
   use the branch that matches the Odoo version, for example `19.0`.
4. Confirm. The panel first checks that it can read the repository.

Adding and removing change the repository on GitHub, so the server needs a deploy key with write access for the
main repository ("Allow write access" ticked on GitHub); the GitHub App only reads. If it is not, GitHub refuses, the panel undoes the change on the
server and tells you so: the server and GitHub never disagree.

---

## 8. GitHub access (private repositories)

Public repositories need nothing. For **private** repositories, including private submodules, the server needs
read access, which it gets in one of two ways:

| Way | What it covers | Who sets it up |
| :--- | :--- | :--- |
| **GitHub App** (recommended) | Every repository that you allow (all, or selected ones) of the one GitHub account or organisation that owns the app, submodules included. Read-only. | A super admin, once, in a few clicks. |
| **Deploy key** (fallback) | Exactly one repository. Read-only unless you allow writing. | Anyone who manages GitHub access, once per repository. |

### Connecting the GitHub App

A super admin connects GitHub once; the administrator's guide has the screens in detail
([The GitHub App](ADMIN_GUIDE.md#15-the-github-app)).

1. Open **GitHub access** (sidebar, Operations) and click **Connect GitHub**.
2. Answer **Whose repositories will the instances use?** A private GitHub App works only on the account that owns
   it, so for a company's repositories choose **An organisation** and type its name, as in `github.com/acme`:
   `acme` (you must be an owner of the organisation on GitHub). Choose **My personal account** only when the code
   is in your own account. Then **Continue on GitHub**.
3. GitHub shows the new app already filled in. Press **Create GitHub App**. GitHub brings you back to the panel,
   which says "GitHub App created".
4. Click **Install on GitHub**, then **All repositories** or **Only select repositories**, and press **Install**.
   GitHub brings you back again and the account appears under **Installed on**.

**Choose repositories** next to the installation opens GitHub's page where you change its repositories later. If you
installed the app on GitHub directly, or GitHub's page did not bring you back, the installation appears by itself
within a minute when you open **GitHub access** or check a repository (**Refresh** reads it at once).

Repositories of other accounts (for example your personal ones when the app belongs to the company) need deploy
keys; the administrator's guide explains the choice
([Which account should own the app](ADMIN_GUIDE.md#which-account-should-own-the-app)).

From then on, the **New instance** page offers your repositories as you type in the repository field, and pushes
reach the panel at once through the app (no webhook per repository).

### Deploy keys

When no GitHub App is connected, every private repository needs a **deploy key**: a key that lets the server read
that one repository and nothing else. See [Deploy keys](concepts/DEPLOY_KEYS.md) for how they work.

You need **admin rights on the repository on GitHub** to add a key. If you do not have them, send the key to
someone who does.

1. Under **Deploy keys** on the **GitHub access** page, click **Add**.
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

The list shows all keys with their repository and creation date. **Delete** removes the server's key; also remove
it on GitHub (repository Settings, Deploy keys). One key per repository is shared by every instance that uses that
repository.

### Switching from deploy keys to the GitHub App

Existing deploy keys keep working: a repository with a deploy key goes on using it. To switch, connect and install
the GitHub App on the repository's owner, then delete the repository's deploy key in the panel and on GitHub. The
instances keep running and take their next update through the app.

The GitHub App only reads. Adding or removing a submodule from the panel writes to the repository, so it still
needs a deploy key with write access for that repository.

---

## 9. Accounts and roles

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
| Restore a backup of an instance (replaces its database and files) | yes | yes | yes | no | no |
| Change an instance's settings tab (automation, a longer lifetime) | yes | yes | yes | no | no |
| Set an instance's resources (workers, limits) | yes | yes | no | no | no |
| Set a project's and a branch's resources within the maxima ([Project resources](#project-resources)) | yes | yes | no | no | no |
| Give an instance a shorter lifetime | yes | yes | no | no | no |
| See the Odoo passwords (Access tab) | yes | yes | yes | no | no |
| GitHub access (deploy keys; see the GitHub App's state) | yes | yes | yes | no | no |
| Connect, install and disconnect the GitHub App | yes | no | no | no | no |
| Delete instances | yes | yes | no | no | no |
| Add and remove an instance's custom domains, choose the primary one | yes | yes | no | no | no |
| Backups page (download, delete) | yes | yes | no | no | no |
| Accounts, API tokens, audit log | yes | yes | no | no | no |
| Teams; a project's access (grants, restricted projects and branches); lock and unlock branches ([Teams](#teams), [Project access and locked branches](#project-access-and-locked-branches)) | yes | yes | no | no | no |
| Add clients to projects and take them out ([Client portal](#16-client-portal)) | yes | yes | no | no | no |
| Ask a client for access to a closed project or branch | yes | yes | yes | yes | yes |
| Emergency access to a project a client closed, where the client allows it | yes | no | no | no | no |
| Create, change and delete super admins | yes | no | no | no | no |
| Settings, restart the panel | yes | no | no | no | no |
| E-mail templates (the texts of the e-mails the panel sends) | yes | no | no | no | no |
| Shell tab: a terminal in the Odoo container, `odoo shell` ([The Shell tab](#the-shell-tab)) | yes | yes | if allowed | if allowed | no |
| Mail tab: read the e-mails caught from staging and development instances ([The Mail tab](#the-mail-tab)) | yes | yes | yes | yes | no |
| Delete and release caught e-mails | yes | yes | no | no | no |
| Shell tab: `psql` in the database container | yes | yes | no | no | no |
| Shells in production instances; root shells (when switched on) | yes | if allowed | if allowed | if allowed | no |
| See open and past shells, end them, play recordings | yes | no | no | no | no |

"If allowed": a super admin decides on the Settings page (**Sign-in and security, Web shell**) whether maintainers, or
maintainers and developers, may open shells too, and whether shells in production instances are for super admins
only (the default), for everyone allowed elsewhere, or for nobody. Root shells are always for super admins only.

API tokens act with a role too: a **write** token as an Administrator, a **read** token as a Viewer, limited by the
role of the account that created it (its owner), whose teams and project access apply to it as well. No API token can
open a shell.

Inside a project, a role is the most someone can do: the project's access can narrow it for a team or a person, hide
a project or a branch, or lock a branch ([Project access and locked branches](#project-access-and-locked-branches)).

A **Client** account is not part of your team: it sees only the projects it is a client of, in the client portal,
and opens or closes access to them; nothing in the table above applies to it ([Client portal](#16-client-portal)).

When you try something your role does not allow, the panel says which permission is missing. Ask an administrator.

Accounts from an earlier version of the panel were renamed automatically: "administrator" became **Super admin**
and "sync operator" became **Developer**.

### The Accounts page

**Accounts** (sidebar, Administration) lists every account with its name, e-mail address (or user name, when it
has none), role, whether it has a second factor and its last sign-in. Your own account is marked **(you)**, and
**new password pending** shows on accounts that must still choose their own password.

| Action | What happens |
| :--- | :--- |
| **Invite** | Enter the person's **e-mail** (no other account may use it) and **role**; their name is optional. They receive an e-mail with a link that works **once** and for `INVITATION_HOURS` (72 hours by default): they choose their password (the [password rules](#password-rules)) and their name, are signed in, and are offered a second factor (asked to set one up first when the panel requires it). Until then the list shows **Invited** and when the link expires. |
| **Send again**, **Revoke invitation** (row menu of an invitation) | Sending again e-mails a new link with a new expiry; the previous link stops working. Revoking makes the link stop working. |
| **New account** (only while e-mail is not set up, or when `INVITATION_PASSWORD_FALLBACK` allows it) | Enter first name, last name, **e-mail** (required: the person signs in with it, and no other account may use it), phone (optional) and a **role**, then choose the password (below). The user name is made from the e-mail address. |
| **Edit** (row menu) | Change the first and last name, the e-mail address, the phone and the role. |
| **Set a new password** (row menu) | Choose a new password the same way as for a new account. The account's current sessions end. |
| **Remove second factors** (row menu) | For a person who lost their phone or mailbox. They sign in with the password only, and set up a new second factor if one is required. |
| **Delete** (row menu) | The account is removed and signed out at once. |

**The password of a new account**, and **Set a new password**, offer two choices:

| Choice | What happens |
| :--- | :--- |
| **Generate** | The panel makes a random password and shows it **once**. Copy it and give it to the person through a safe channel. At the first sign-in they must choose their own. |
| **Type a password** | Type it twice. It is checked as you type against the [password rules](#password-rules), and a meter of four bars shows its strength: **Acceptable**, **Good**, **Strong**, **Very strong**. **Suggest a strong one** fills in a random password with a copy button. The switch **Ask them to choose their own password when they first sign in** is on by default; turn it off only when the person keeps the password you typed. |

In both cases the password is shown once after you click **Create** or **Set password**.

Rules:

* Only a super admin can create, change or delete a super admin. A super admin can edit other super admins.
* You can edit your own account here: name, e-mail, phone and role. Its menu holds only **Edit**; to change your
  password or second factors use **My account**, and you cannot delete your own account.
* Changing your own role asks for confirmation and signs you out; you sign in again with the new role. When the role
  of another account changes, that account is signed out too.
* There is always at least one super admin: the last one cannot be given another role or be deleted.
* Two accounts cannot share an e-mail address.

Passwords are never stored by the panel, only checked: see
[Passwords and encryption](concepts/PASSWORDS_AND_ENCRYPTION.md).

### Teams

> Teams and access per project are premium features: they install automatically from Sudoo.sh when your plan
> includes them ([Premium features](PREMIUM.md)). Branch locks are on every plan.

**Teams** (sidebar, Administration; administrators) are named groups of accounts, for example the leads who release
to production, a project's developers, or a client's reviewers. **New team** asks for a name, an optional description
and the members (a list of accounts with a search). The list shows each team's members and the projects whose access
or locks name it; **Edit** and **Delete** are in its row menu.

A team gives nothing by itself: a project's **Access** tab gives it a level in the project or in one of its branches,
and a branch's lock can allow it. Deleting a team removes it from every project's access and every lock; deleting
an account removes it from every team, access and lock. Every change is in the audit log, with the members added and
removed.

### Project access and locked branches

What someone may do in a project is **their role, limited by the level they have there**. A level never gives more
than the role: a Viewer given the level Admin is still a Viewer.

| Level | What it allows (within the role) |
| :--- | :--- |
| **No access** | Nothing: the project (or the branch) is not shown at all. |
| **View** | See the instances, their activity and logs. |
| **Developer** | Also get new code, restart and start, read caught e-mails, open the Odoo container's shell. |
| **Maintainer** | Also create, operate and restore instances, see the Odoo passwords, change the project's settings and the stages of its branches. |
| **Admin** | Everything the role allows in the project, its access and locks included. |

A project's **Access** tab (administrators) chooses:

* **Who sees the project.** **Everyone** (every project starts so, and every project made before teams existed):
  everyone works with their role, and grants change it only for the teams and people they name; a grant of
  **No access** hides the project from them. **Only those granted**: only the teams and people listed see it.
* **Grants.** A team or a person, with a level. A person's own grant comes first; otherwise the highest level of
  their teams applies.
* **Branches.** A branch can give its own levels (they replace the project's for whoever they name), and it can be
  **Restricted**: only its own grants see it, for example production for the leads. The rest of the project stays as
  it is.

Super admins always see every project and branch. The panel refuses a change that would leave you unable to manage
the project's access yourself: grant yourself (or one of your teams) the Admin level first.

**Locked branches.** A branch's menu on the **Branches** tab has **Lock**: give a reason (optional) and the people and
teams it allows (you stay allowed). While a branch is locked, nobody else may open its instances from the panel (the
Odoo link, the Access tab, logs, caught e-mails, the shell), change, update, deploy, back up, restore or delete them,
change the branch's stage, create an instance of it or copy its data into staging (when it is production). Everyone who
sees the branch sees the lock: a **Locked** tag on the branch and its instances (who locked it and why on hover), and
a banner on the instance's page. Super admins are locked out too until they **Unlock**, which they always may; the
others need to be allowed by the lock, and **Change the lock** follows the same rule. Locking, changing and unlocking
are recorded in the audit log.

The panel's own safety work goes on while a branch is locked: a locked production branch keeps serving, is never
paused, and gets its daily backup; monitoring and certificates continue. Automatic changes stop: pushes do not
update its instances (or rebuild its staging), an expired lifetime does not delete them, a branch deleted on GitHub
does not archive or delete them, and a visit to a paused instance of it shows **Locked** instead of starting it.

### API tokens

**API tokens** (sidebar, Administration) let other systems, such as a CI pipeline, create and manage instances
through the panel's API. Click **New token**, give it a name and a scope (**Read only**, **Instances** for a
connected app such as the Odoo module, **Developer** for developers' tools and CI, or **Read and write**); copy the
token and the signing secret, shown once, into the other system. A **Developer** token reads projects, branches,
instances, logs, jobs and caught e-mails, starts, pauses, restarts and updates instances (latest code, submodules) and
creates development instances; it never changes settings, accounts, teams, access or stages, never touches
production, restores, domains, off-site copies or shells. No token ever does more than its creator's role and project
access allow. The list shows when each token was last used (to the minute).
**Revoke** stops a token at once. See [Panel API](PANEL_API.md).

---

## 10. My account

Click your name at the bottom of the sidebar.

**Profile.** First name, last name, e-mail and phone. Click **Save**. While your e-mail address receives your
sign-in codes it is locked here; to change it, remove the e-mail code and set it up again with the new address.
An address that another account uses is refused. A new address signs out your other sessions.

**Password.** Enter your current password and the new one twice, then click **Change password**. The new password
is checked as you type against the [password rules](#password-rules), and a meter of four bars shows its strength
(**Acceptable**, **Good**, **Strong**, **Very strong**). Changing your password signs out your other sessions.

**Second factor.** A second factor asks for a 6-digit code after the password, so a stolen password alone is not
enough.

| Factor | Setting it up |
| :--- | :--- |
| **Authenticator app** | Click **Set up**, scan the QR code with an app such as Google Authenticator, Microsoft Authenticator or 1Password, and type the code it shows. |
| **E-mail code** | Click **Set up**, check the address, click **Send a code**, and type the code from the e-mail. Only offered when the server can send e-mail. |

**Replace** sets a factor up again, for example on a new phone: type your password, then follow the same steps.
Your other sessions end. **Remove** turns a factor off; type your password to confirm. Your other sessions end. If
your role requires a second factor, keep at least one.

**Recovery codes.** When you set up the authenticator app, the panel shows **10 recovery codes** (the administrator
can choose another number). Each one signs you in once, in place of the app's code, if you lose your phone. They are
shown only that once:

1. Click **Copy all** or **Download** and keep them in a password manager or another safe place, away from your
   phone.
2. Click **I saved them**.

The **Recovery codes** line under your second factors says how many are left. **New codes** (type your password)
makes a new set and the old codes stop working; do this when few are left or if someone may have seen them.
Removing the authenticator app removes its codes too. If your account used the app before recovery codes existed,
a notice at the top of every page asks you to make them.

**Sign-in alerts.** **E-mail me at each sign-in** turns the sign-in e-mail on or off for your account. The switch is
locked when the administrator keeps the e-mail on for everyone, and nothing is sent while e-mail is not set up on the
panel. **Sign out others** ends every other session of your account, for example a browser you left signed in;
this one stays signed in.

### Password rules

The same rules apply everywhere a password is chosen: the first run, a new account, **Set a new password** and
**My account**. They follow the NIST guidelines for passwords (SP 800-63B): length counts, forced mixes of
characters do not.

| Rule | Details |
| :--- | :--- |
| Length | 12 to 128 characters. |
| Characters | Any, including spaces. You do not need capitals, digits or symbols. |
| Common passwords | Refused when it contains a common password or word, such as `password`, `qwerty`, `admin`, `odoo`, `sudoo` or `welcome`. |
| Sequences | Refused when it contains five characters in a row of the alphabet, the digits or a keyboard row, in either direction, such as `abcde`, `12345`, `54321`, `qwerty`, `asdfg` or `zxcvb`. |
| Repeats | Refused when one character appears four times in a row, such as `aaaa`. |
| Variety | At least 6 different characters. |
| Personal details | Refused when it contains your first name, last name, user name or the part of your e-mail address before the `@` (each counted when 3 characters or longer). |

Pasting a password and using a password manager both work. A short sentence is the easiest kind of password to
remember and the hardest to guess, for example `green tea at 7 on the balcony`.

---

## 11. Backups and audit log

### Backups

**Backups** (sidebar, Operations) lists every backup: instance, database, **Kind**, when it was taken and its
size. An instance's own backups are also on the **Backups** card of its **Overview** tab
([The Backups card](#the-backups-card)).

| Kind | When it is taken | How long it is kept |
| :--- | :--- | :--- |
| **By hand** | **Back up now** on an instance's page. | Until someone deletes it. |
| **Daily** | Once a day, for each instance that was used since its last daily backup (below). | Only the latest per instance: a new daily backup replaces the previous one. Removed when the instance is deleted. |
| **Before deletion** | Before every deletion, automatic or by hand. If the backup fails, the instance is not deleted. | Until someone deletes it. |
| **Before a restore** | At the start of every restore: the state just before it, offered as **Rollback**. | Only the latest per instance, for 24 hours (`ROLLBACK_HOURS`). Removed when the instance is deleted. |
| **Before a rollback** | At the start of every rollback: the state just before it, so a rollback can be undone by hand. | Only the latest per instance, for 24 hours (`ROLLBACK_HOURS`). Removed when the instance is deleted. |

**Daily backups.** Every day at the time set under **Settings, Backups** (00:00 UTC by default), the panel backs up
each instance that was used since its last daily backup. An instance counts as used when someone opened it through
its link, or when it was started or restarted. An instance nobody used gets no new daily backup, so its last one
stays. A running instance keeps running during the backup, and a paused one stays paused. If the panel was not
running at that time, the backups start as soon as it is back. When an instance is deleted, its daily backup goes
with it: the backup taken before the deletion is newer and is kept.

Backups taken by hand and before deletions are kept until someone deletes them, so look at the list from time to
time; the disk usage is on the Instances page.

What a backup contains: one zip file in Odoo's own backup format, with

| Part | Contents |
| :--- | :--- |
| `dump.sql` | The whole database. |
| `filestore/` | The attachments and images that Odoo keeps outside the database. |
| `manifest.json` | The database name, the Odoo version and (backups of this version of Sudoo.sh on) the edition. |

**Download** saves the zip. **Delete** removes a backup you no longer need.

**Restoring an instance.** To put an instance back to an earlier state, use **Restore the latest** or **Restore**
on its **Backups** card ([Restoring](#the-backups-card)). The current state is saved first as a **Before a
restore** backup, and the instance offers **Rollback** for a while. **Restore another backup…** puts a backup of
another instance of the same Odoo version into it.

**A new instance from a backup.** On the **New instance** page, switch on **Start from a backup** and pick one of
the backups of the chosen Odoo version: the instance starts with that data instead of an empty database
([section 5](#5-creating-an-instance)).

**Off-site copies.** When the administrator has set them up, backups are also copied to storage away from the
server (every backup of a production instance; the others when the administrator chooses so). The **Backups**
page then groups everything by instance and shows where each backup is: **This server** or **Off-site**, with the
off-site copy's status:

| Status | Meaning |
| :--- | :--- |
| **Uploading** | Being copied. |
| **Stored** | Complete in the off-site storage. |
| **Verifying** | Being downloaded and checked. |
| **Verified** | Downloaded again and checked: its signature and every checksum match (the date says when). |
| **Failed** | The copy or its check failed; the reason is shown next to it. The backup on the server is not affected. |

On an off-site copy, **Verify now** checks it again, **Copy again** retries a failed copy (while the backup is still
on the server), and **Restore** puts it back into an instance: the copy is downloaded and checked first, and if
anything does not match, nothing is restored. After that it works like any restore: the current state is saved
first and **Rollback** is offered. Restoring needs the right to restore instances and off-site copies
(administrators); deleting a copy needs the right to delete off-site copies. See
[Admin guide, Off-site copies](ADMIN_GUIDE.md#20-off-site-copies).

**Restoring into another Odoo.** The zip also works in any Odoo of the same version, for example to make a copy:

1. Open an Odoo of the **same version** (an instance's link, or any other Odoo) at `/web/database/manager`.
2. Click **Restore Database**.
3. Enter the master password (on the instance's **Access** tab), choose the zip file and a database name.
4. Say whether the database was **moved** (it replaces the original) or is a **copy** (a second database next to
   the original), then confirm.

Restoring brings back both the data and the attachments, because both are in the zip.

### Audit log

**Audit log** (sidebar, Operations) lists what happened, newest first: sign-ins (also failed ones), refused actions,
every job on an instance, viewing an instance's passwords, account, key, token and settings changes. Each line shows
when, the account, what happened, the instance (or project and branch) and the address it came from. A changed
secret setting (a password or webhook secret) appears as `(set)`, never with its value.

Filter by **Project** and **Branch**, **Instance**, **User**, **Action**, **From** and **To** dates and **Result**
(allowed or refused), or search any text; click a column's title to sort by it, and page through the results.
The filters are part of the page's address, so a filtered view can be bookmarked or shared; a project's page links
to its own entries (**Audit log**, under its name). **Export** downloads what the filters show as CSV or Excel
(XLSX), a year at most; each export is recorded in the audit log too.

### Alerts (the bell)

The bell at the top right lists the latest alerts: a job that failed, an update that was rolled back, a failed
backup, an instance that is down, the disk almost full, memory high, a change made outside the panel, a missing
public link, and a **Resolved** line when such a problem is over. A dot on the bell means there are alerts you have
not seen yet; opening the list clears it. Click an alert to open the instance or the job it is about. Super admins
and administrators see the bell (everyone who may read the audit log). Who also gets alerts by e-mail or in Slack,
Discord or Teams is set by a super admin under **Settings**, **Alerts**.

---

## 12. Settings

Super admins only. **Settings** (sidebar, Administration) shows every setting of the panel that can be changed in
the browser, in tabs:

| Tab | What it holds |
| :--- | :--- |
| **General** | Contact and appearance: who people are told to contact, and the accent colour of the panel and its e-mails. |
| **Instances** | What a new instance starts with (pause when idle and after how many hours, automatic updates, upgrades, lifetime, Odoo login); how many instances in total and per person, and how often a new one can be created; the process limit and logs of each instance's containers; what visitors may send through the gateway. 0 turns a limit off. |
| **Resources** | How instances share the server when it is busy (production first, then staging, then development) the default limits of each stage, all off by default, with the most project owners may set, how container limits follow the workers, and the storage quotas ([Administrator guide: Resources and priority](ADMIN_GUIDE.md#resources-and-priority)). |
| **Odoo** | Which versions are offered, where Odoo and PostgreSQL come from, and where the Enterprise add-ons are (including the Enterprise source plugin). |
| **Backups** | Whether daily backups are taken, at what time (`HH:MM`, 24-hour clock) and in which time zone (for example `Africa/Cairo`; UTC by default), and how long a restore can be rolled back. Changes apply at once. |
| **Sign-in and security** | Who must use a second factor (none, administrators, everyone), which second factors are offered, the authenticator, how long a session lasts, the sign-in e-mail (on for everyone by default, and whether each person may turn it off), how many recovery codes an authenticator app comes with, and requests per minute with the rate limiter. |
| **Addresses and domains** | How people reach this server (**Direct**, **Proxy** or **Tunnel**, applied at a restart, with what a change does shown before it is saved), the panel's name and the server's public name or address, custom domains, certificates and managed subdomains ([Public addresses](concepts/PUBLIC_ADDRESSES.md)). |
| **E-mail** | The mailer and the mailbox that sends sign-in codes, with **Send a test**, and a link to **E-mail texts** ([Administrator guide: E-mail templates](ADMIN_GUIDE.md#17-e-mail-templates)). |
| **Alerts** | Who is told when something needs attention (super admins, more addresses, up to three webhooks for Slack, Discord, Teams or your own system), which alerts are sent, their thresholds, how often the same alert may repeat, and a daily summary, with **Send a test alert** ([Administrator guide: Alerts](ADMIN_GUIDE.md#16-alerts)). |
| **GitHub and updates** | How often GitHub is checked, the update triggers, and the webhook secret. |
| **Marketplace** | The connection to the Sudoo.sh marketplace, and linking this panel to an account ([Marketplace](MARKETPLACE.md)). |
| **Plan** | The panel's Sudoo.sh plan, as the signed licence from Sudoo.sh says: what it allows (production branches, servers, projects, teams and access per project, custom domains, off-site backups, support), what the panel uses (production branches by name, instances by stage), whether new production branches are withheld, which production branches leave production after a downgrade, and **Manage the plan** and **Compare plans** ([Plans and limits](#plans-and-limits)). |
| **Advanced** | How often figures are measured, the tunnel connector image, **Restart the panel**, and **Server**: read only, the folders, ports and how the panel is reached, which stay in the server's `.env`. |
| **About** | The licence agreement: the version this release bundles, the **Required licence version** (a newer one asks super admins to accept again), who accepted it, when and for which use, every acceptance (in the panel and by the installer, and whether your Sudoo.sh account has it), the full text, and **Review and accept again** (super admins) ([Administrator guide: The licence agreement](ADMIN_GUIDE.md#22-the-licence-agreement)). |

The chosen tab is part of the address (for example `/settings?tab=backups`), so it can be linked and survives a
reload. Changes can be made in several tabs and saved together: the bar at the bottom counts them and names the tabs,
and a dot after a tab's name marks unsaved changes in it (accent) or a change waiting for a restart (amber). The
arrow keys move between tabs; on a narrow screen the tabs scroll sideways.

### Plans and limits

What the panel may run is its **Sudoo.sh plan**. A panel linked to a Sudoo.sh account (Settings, Marketplace) gets
its plan from Sudoo.sh with every heartbeat, signed, so it cannot be changed in the panel; a panel that is not linked
has the **Free** plan of the licence: one production branch on one server, the paid features off. A banner tells
super admins so, with a link to link the panel.

**Link within 7 days.** A panel is linked to a Sudoo.sh account within 7 days of its first run (`LINK_DEADLINE_DAYS`,
Settings, About). Until then, super admins and administrators see a banner on every page, **Link this panel to your
Sudoo.sh account**, with the days left; it cannot be closed. Once the days have passed and the panel is still not
linked, no new instances, no moves into production and no updates (latest code, submodules, switch branch, upgrade)
can be made until a super admin links it; instances keep serving and backups keep running. Linking lifts this at
once ([Administrator guide](ADMIN_GUIDE.md#linking-the-panel-within-7-days)).

* **Production branches.** Every production branch of every project, and every production instance outside a project,
  counts. One more (a branch made production, a new production instance) is refused beyond the plan's number, with a
  sentence that says how to get more; extra branches bought and privileges granted by Sudoo.sh are in that number.
  Development and staging branches are never limited by the plan.
* **Paid features.** Teams, access per project and per branch, adding custom domains and turning off-site backups on
  come with Pro and Business. On a plan without them the page says so (**Compare plans**), and what was set up before
  keeps working: domains keep serving and their certificates keep renewing, off-site copies already made stay where
  they are, access rules and locks stay (and can always be removed). Locked branches are on every plan.
* **Withheld production.** While the Sudoo.sh account must still declare its use, verify its company or accept the
  current licence, new production branches are withheld; the production branches you have keep serving.
* **When Sudoo.sh cannot be reached,** the last plan applies for its validity (24 hours by default); after that new
  production branches wait until the panel hears from Sudoo.sh again. Nothing that runs is ever stopped.
* **After a downgrade** that left more production branches than the new plan allows, choose which to keep on the
  account site (**Choose the branches to keep**). When the downgrade applies, the others become **development**
  branches: their instances keep their data, keep running and are never deleted, and an alert and the audit log say
  which. Settings, Plan lists them beforehand.

How changes apply:

* **At once.** Almost every change applies as soon as you save it. It is kept on top of the server's `.env` file.
* **Use .env** next to a changed setting brings back the value from `.env`.
* **Passwords** (mail password, Odoo admin password, webhook secrets, alert webhook addresses) are write-only: the page shows whether one is
  set, never the value. They are stored encrypted.
* **Plugins** (settings tagged "plugin, needs a restart": the mailer, second factors, authenticator, rate limiter,
  update triggers and Enterprise source) apply after **Restart the panel**: a notice with the button appears above
  the tabs while a restart is waiting, and the button is always under **Advanced**. The restart stops only the
  panel's backend for a few seconds; instances keep running. It is refused while jobs run.
* **Server** settings (Advanced tab) can only be changed by the server administrator, in `.env` on the server.

**Send a test** (E-mail tab) sends a test message with the saved settings to an address you enter. If it fails,
the message says why; for Gmail, the password must be an app password
([Administrator guide: E-mail](ADMIN_GUIDE.md#6-e-mail)).

### Support access

When you ask Sudoo.sh support for help, you can let them into the panel for a while: **Settings › Support access**
(administrators: **Support access** in the side bar). Choose what they may do (**View**, **Panel admin**, or
**Panel admin + shell**), for how long (1 hour, 1 day or 3 days), where (all projects and instances, or the ones you
choose; locked branches stay locked), and, if you have one, the ticket number. Then **Grant access**.

While support is connected, every page shows **Sudoo.sh support is connected** with their name. Press **End now**
to end it at once; **Withdraw** ends one grant. Everything they do is in the audit log, marked **Support**, and their
shells are recorded. They cannot see or change accounts, API tokens, teams, access rules or the panel's settings.

Every setting is described in [Configuration](CONFIGURATION.md).

---

## 13. Help pages and appearance

**Help** (sidebar) shows this documentation inside the panel: the same pages as in the project's `docs` folder,
with a table of contents and an **On this page** list of the current page's sections. Links between the pages work
as in the repository.

### The help button

The **?** button at the top right of every page opens the help panel at the side (press **?** on the keyboard
anywhere but in a text field; **Esc** closes it). It has:

* **On this page**: a few sentences about the page you are on, and links to the matching part of these pages, in
  the panel and on the documentation site.
* **Keyboard shortcuts** the panel knows.
* **Contact support**: the Sudoo.sh help desk when the panel is linked to a Sudoo.sh account, otherwise the help
  address or e-mail your administrator set (**Settings, General, Help**).
* The panel's **version**, and **Copy diagnostics**: the version, your browser, the page, the time and the request
  id of the last error the panel showed you, to paste into a message to support. It holds no password, address of
  an instance or other secret.

**Theme.** The button at the bottom of the sidebar switches between **System** (follows your computer's setting),
**Light** and **Dark**. The choice is kept in your browser. The accent colour is chosen by a super admin
(**Settings, General**).

**Language.** The panel is in English and Arabic (right to left). The button next to the theme button at the bottom
of the sidebar switches to the other language, and **My account, Language** chooses one, or the panel's default
(**Settings, General, Default language**). The choice is kept on your account, so every browser you sign in with
shows the panel in it. Before you sign in, the sign-in page uses your browser's language, and has the same switch.
Code, branches, addresses, logs and e-mail addresses stay left to right in Arabic, digits are 0-9, and messages
written by the server itself (a job's log, an error's explanation, Odoo's logs) are shown as the server wrote them,
in English. E-mails the panel sends are in English for now.

---

## 14. When something goes wrong

1. Read the message: the panel explains failures in plain words.
2. Open the job in the instance's **Activity** tab to see **Why it failed**: which step failed, the reason and the error details. The full technical log is in the server's log; your administrator can read it.
3. Try **Restart** if Odoo behaves strangely, and look at the **Logs** tab if it does not start.
4. If a button is missing or an action is refused, your role may not include it ([section 9](#9-accounts-and-roles)).
5. If it is still not solved, contact the administrator named on the sign-in page, and say which instance, what you
   did and what the message said.

Common messages are listed in the [Administrator guide's troubleshooting table](ADMIN_GUIDE.md#13-troubleshooting).

---

## 15. Questions and answers

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
Ask an administrator to use **Set a new password** on your account. You get a new password once and usually choose
your own at the next sign-in.

**What do I type in the E-mail field when I sign in?**
Your e-mail address. Your user name works too, for example on an older account without an e-mail address.

**Why is my password refused?**
The message says which [password rule](#password-rules) it breaks. Most often it contains a common word, a sequence
such as `12345`, or your name. A short sentence of unrelated words is accepted and easy to remember.

**Does a backup include the attachments?**
Yes. The zip holds the database (`dump.sql`), the filestore with the attachments, and a manifest
([section 11](#11-backups-and-audit-log)).

**When are backups taken?**
Once a day for every instance that was used since its last daily backup (at 00:00 UTC unless a super admin changed
it), before every deletion, at the start of every restore, and whenever someone clicks **Back up now**
([section 11](#11-backups-and-audit-log)).

**How do I restore an instance?**
Open the instance, **Overview** tab, **Backups** card. Click **Restore the latest**, or **Restore** next to an older
backup, and confirm. The current state is saved first as a **Before a restore** backup, so you can go back. It takes
a minute or two ([The Backups card](#the-backups-card)).

**I restored the wrong backup. Can I undo it?**
Yes, for 24 hours (unless your administrator changed it): click **Rollback to before the restore** in the banner at
the top of the instance's page. It puts back the state just before the last restore.

**Can I copy one instance's data into another?**
Yes, if both run the same Odoo version: on the target instance's **Backups** card click **Restore another
backup…**, or start a new instance from the backup on the **New instance** page. The copy is neutralised: it sends
no e-mail and runs no scheduled actions until you switch them back on. Administrators and super admins can do this.

**Can another system send webhooks to an instance, or call its API?**
Yes, through the instance's link. See [API and webhooks](API.md). Remember the link can change after a server
restart.

**Is my data safe in an instance?**
Instances are for showing and testing Odoo. Anyone with the link and the Odoo login can sign in. Do not put real
customer data in them.

---

## 16. Client portal

The client portal is what your **clients** see: the Odoo projects you run for them, and a switch that decides
whether your team may open them. A client signs in at the portal's own address when your administrator set one
(`PORTAL_DOMAIN`, such as `portal.example.com`), otherwise at the panel's address; either way a client account sees the
portal and nothing else of the panel.

### For the client

**Your projects** lists each project you are a client of, with its switch (**Open**, **Closed** or **Open until** a
time), the branches you closed, the requests waiting for you, and a mark while emergency access is in use. Open a
project to see:

* **Access by your provider's team**: the switch of the project as a whole.
  * **Open**: your provider's team works with their usual access.
  * **Closed**: only you and the people you let in may open, change, back up, restore, open a shell, read logs or
    see passwords. Everyone else, your provider's super admins included, is refused, and each attempt is in your
    activity log. Daily backups, monitoring and certificates go on, and production keeps serving. Closing also ends
    the access you gave before.
  * **Open for a time**: 1, 4 or 24 hours (your provider may offer others), or a number of hours you type; then it
    closes again by itself.
* **Branches**: each branch follows the project unless you give it its own setting, for example production closed
  while development stays open. Each instance shows its status and **Open Odoo**.
* **Access requests**: your provider's team asks before opening something you closed, with a reason and a
  duration. **Approve** lets them in (you may choose other people of their team and a shorter time) and it closes
  again by itself; **Refuse** keeps it closed. You get an e-mail for each request.
* **Who can get in now**: the approved requests and emergency access in use, with **End now**.
* **People you let in**: people of your provider's team who may work here even while it is closed.
* **Emergency access**: allowed by default. If production breaks, one of your provider's super admins can get in
  for a short time (an hour unless they changed it) by writing a reason and typing the branch's or project's name.
  You get an e-mail at once and it shows in red at the top of the project and of your log. Turn it off and nobody of
  your provider can get in while you keep the project closed, even in an emergency.
* **Backups**, when your provider gave them to you: download a copy of your database and files.
* **Activity log**: everything done in the project: opening and closing, requests and answers, emergencies, and
  every time someone opened data (passwords viewed, shells, backups downloaded, restores), allowed or refused.
  **Access only** keeps the access events; **Export CSV** downloads it (a year at most).

**What the switch protects.** Closing a project stops everyone in the panel, its super admins included, and every
attempt is recorded. It is a control inside the panel: whoever administers the server itself (its root account or
Docker) can still reach the data without the panel. For stronger isolation, run your projects on a server of your
own.

### For your team

A project with a client shows **Closed by client** on a closed branch and its instances, and a notice with
**Request access** on the project, the branch and the instance: write why you need it and for how many hours; the
client gets an e-mail and answers in the portal. Super admins also see **Emergency access** while the client allows
it. The project's **Clients** tab lists its clients, the requests and their answers, and the access in use;
administrators add a client there (**Add a client**: an existing client account at once, a new address by e-mail
invitation; the client may be given backups and a shell) and take one out. Nobody of your team can lift a client's
closure, change or reset a client's account while it keeps something closed, or add another client to a closed
project. See [Admin guide, Client portal](ADMIN_GUIDE.md#30-client-portal).
