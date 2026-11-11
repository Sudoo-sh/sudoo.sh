# Git Submodules

**In one sentence:** a submodule is another repository placed inside your repository at a fixed version, so a
project can include, for example, OCA module collections without copying their files.

---

## Contents

1. [An everyday comparison](#1-an-everyday-comparison)
2. [What a submodule really is](#2-what-a-submodule-really-is)
3. [Example: an Odoo project with OCA modules](#3-example-an-odoo-project-with-oca-modules)
4. [What the panel's buttons do](#4-what-the-panels-buttons-do)
5. [How Odoo finds the modules](#5-how-odoo-finds-the-modules)
6. [Common surprises](#6-common-surprises)

---

## 1. An everyday comparison

A recipe book that says "for the sauce, see page 42 of *Classic Sauces*, 3rd printing" instead of copying the sauce
recipe. The book stays small, the sauce recipe is maintained by its authors, and naming the exact printing means
your dish never changes by surprise when they publish a new edition. Moving to a newer printing is a decision you
make.

---

## 2. What a submodule really is

A submodule is two small pieces of information stored in the main repository:

| Piece | Where | Example |
| :--- | :--- | :--- |
| **Where it comes from and where it goes** | the file `.gitmodules` | address `https://github.com/OCA/web`, folder `oca/web`, branch `19.0` |
| **Which exact version** | a pointer to one commit | `oca/web` is at commit `4f2a9c1` |

`.gitmodules` looks like this:

```ini
[submodule "oca/web"]
    path = oca/web
    url = https://github.com/OCA/web
    branch = 19.0
```

The pointer is why a submodule does **not** change by itself when OCA publishes something new. It changes only when
someone moves the pointer, and that change is committed like any other change.

---

## 3. Example: an Odoo project with OCA modules

```
acme-co/sales                  (main repository, branch 19.0)
├── sales_quote/                our own module
├── sales_stock/               our own module
├── oca/
│   ├── web/                   submodule → OCA/web @ 4f2a9c1
│   │   ├── web_responsive/
│   │   └── web_dialog_size/
│   └── server-tools/          submodule → OCA/server-tools @ 9be01d3
│       └── base_technical_user/
└── .gitmodules
```

When the panel creates an instance from this repository, it clones it **with** its submodules
(`git clone --recurse-submodules`), so all three repositories arrive, each at its recorded version.

---

## 4. What the panel's buttons do

| Button | Git commands, in short | Result |
| :--- | :--- | :--- |
| **Get latest code** | `git pull`, `git submodule update` | Main repository at its newest commit; submodules at the versions the main repository points to. |
| **Update submodules** | `git submodule update --remote` | Every submodule moves to the newest commit of its branch. |
| **Add submodule** | `git submodule add`, `git commit`, `git push` | A new repository is included, and the change is saved on GitHub. |
| **Remove** | `git rm <folder>`, `git commit`, `git push` | The submodule is removed, here and on GitHub. |

Adding and removing push to GitHub, so the server's deploy key for the main repository needs write access
([Deploy keys](DEPLOY_KEYS.md#6-read-only-or-write-access)). If the push is refused, the panel undoes the change on the
server.

---

## 5. How Odoo finds the modules

Odoo looks for modules in the folders listed in `addons_path`. A submodule usually holds modules one level down
(`oca/web/web_responsive`), so its folder has to be listed. The panel finds every folder that contains modules and
writes `addons_path` itself, on every start and update:

```
addons_path = /mnt/extra-addons,
              /mnt/extra-addons/oca/web,
              /mnt/extra-addons/oca/server-tools,
              /mnt/enterprise-addons               (Enterprise instances)
```

Nothing has to be configured by hand when a submodule is added.

---

## 6. Common surprises

| Surprise | Explanation |
| :--- | :--- |
| OCA published a fix but the instance does not have it | Submodules stay at their recorded version. Use **Update submodules**. |
| "Update submodules" brought a change that breaks something | It moves to the newest commit of the branch. Moving back is done by a developer in the main repository. |
| A private submodule says "No access" | Each private repository needs its own deploy key ([Deploy keys](DEPLOY_KEYS.md#5-example-an-instance-with-a-private-submodule)). |
| An OCA module is missing in Odoo's Apps | Use the OCA branch that matches the Odoo version (`19.0` for Odoo 19), then **Update Apps List** in Odoo. |
