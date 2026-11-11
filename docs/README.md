# Documentation

Everything about Odoo Instance Manager, in one place. The same pages are on GitHub and inside the panel (top menu,
**Documentation**).

---

## Start here

| If you are... | Read |
| :--- | :--- |
| New to the panel | [Project overview](../README.md), then the [User manual](USER_MANUAL.md) |
| A sync operator (you keep instances up to date) | [User manual](USER_MANUAL.md): sections 1, 2, 3, 5 and 6 |
| The administrator of the panel | [User manual](USER_MANUAL.md), then the [Administrator guide](ADMIN_GUIDE.md) |
| A developer connecting another system to an instance | [API and webhooks](API.md) |
| A developer changing the panel | [Architecture](ARCHITECTURE.md) and [Security](SECURITY.md) |
| Reviewing security | [Security](SECURITY.md) |

---

## Find an answer

| I want to... | Where |
| :--- | :--- |
| Create an instance | [User manual: Creating an instance](USER_MANUAL.md#4-creating-an-instance) |
| Give a private repository to the server | [User manual: GitHub access](USER_MANUAL.md#7-github-access-private-repositories) |
| Get a developer's latest push into an instance | [User manual: Keeping the code up to date](USER_MANUAL.md#5-keeping-the-code-up-to-date) |
| Add an OCA repository | [User manual: Submodules](USER_MANUAL.md#6-submodules) |
| Understand a status (Paused, Switched off...) | [User manual: Statuses](USER_MANUAL.md#3-an-instances-page) |
| Create an account or reset a password | [User manual: Accounts and passwords](USER_MANUAL.md#8-accounts-and-passwords) |
| Find the current links after a restart | [Administrator guide: Links](ADMIN_GUIDE.md#4-public-links) |
| Choose how long an instance lives | [User manual: Creating an instance](USER_MANUAL.md#4-creating-an-instance), step 6 |
| Find, download or restore a backup | [User manual: Backups and audit log](USER_MANUAL.md#9-backups-and-audit-log) |
| Back up an instance or the panel | [Administrator guide: Backups](ADMIN_GUIDE.md#7-backups) |
| Add a new Enterprise version | [Administrator guide: Enterprise add-ons](ADMIN_GUIDE.md#6-odoo-enterprise-add-ons) |
| Fix a problem | [Administrator guide: Troubleshooting](ADMIN_GUIDE.md#10-troubleshooting) |
| Call an instance from Python or Postman | [API and webhooks](API.md) |
| Use open ports instead of Cloudflare, or keep Enterprise in git | [Plugins](PLUGINS.md) |
| Look up a setting | [Configuration](CONFIGURATION.md) |
| Know why something was built this way | [Architecture: Why we chose this](ARCHITECTURE.md#6-why-we-chose-this) |

---

## Concepts explained with examples

Short explanations of the ideas the panel is built on, written for people who have not met them before. Each one
starts with an everyday comparison, then shows what it looks like on our server.

| Concept | In one sentence |
| :--- | :--- |
| [Tunneling](concepts/TUNNELING.md) | How a server that accepts no incoming connections can still have a public https link. |
| [Docker and containers](concepts/DOCKER.md) | How many Odoo versions run side by side on one server without disturbing each other. |
| [The gateway (reverse proxy)](concepts/GATEWAY.md) | The small program in front of every instance that records visits and wakes paused instances. |
| [Deploy keys](concepts/DEPLOY_KEYS.md) | How the server reads a private GitHub repository without anyone's password or token. |
| [Git submodules](concepts/GIT_SUBMODULES.md) | How one repository includes others, such as OCA module collections. |
| [Passwords and encryption](concepts/PASSWORDS_AND_ENCRYPTION.md) | Why panel passwords are hashed, why other secrets are encrypted, and the difference. |
| [Web attacks and how the panel stops them](concepts/WEB_ATTACKS.md) | Password guessing, forged requests, injected scripts and commands, with examples. |

---

## All documents

| Document | Contents |
| :--- | :--- |
| [Project overview](../README.md) | What the panel does, technologies and what we gain from them, components, installation |
| [User manual](USER_MANUAL.md) | Every page and button, step by step |
| [Administrator guide](ADMIN_GUIDE.md) | Server, installation, settings, files, links, backups, Enterprise, troubleshooting |
| [API and webhooks](API.md) | Odoo's APIs through an instance's link, with examples |
| [Architecture](ARCHITECTURE.md) | Components, request flow, life of an instance, decisions and their reasons |
| [Security](SECURITY.md) | What is protected, how, and what is not |
| [Configuration](CONFIGURATION.md) | Every setting, with its type and default |
| [Plugins](PLUGINS.md) | Choosing how instances are reached and where Enterprise comes from; writing your own |
