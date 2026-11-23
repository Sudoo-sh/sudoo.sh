# Security Policy

The panel manages Docker on its server (and, with the Kubernetes engine beta, a k3s cluster through a ServiceAccount
limited to its own namespaces instead of the Docker socket: [docs/SECURITY.md](docs/SECURITY.md#24-kubernetes-engine-beta)) and stores the credentials of its GitHub App, deploy keys and Odoo master passwords, so security problems are taken seriously. Thank you for reporting them responsibly.

## Supported versions

Only the latest release receives security fixes. `sudo sudoo-install update` updates a server to it. How the release
images and files are signed, and how to check them: [docs/SECURITY.md](docs/SECURITY.md#13-releases-and-signatures).

## Reporting a vulnerability

**Do not open a public issue, discussion or pull request for a security problem.**

Report it privately through GitHub:

1. Go to the repository's **Security** tab.
2. Choose **Report a vulnerability**.
3. Describe the problem, the steps to reproduce it, what an attacker could do with it, and the commit you tested.

Please do not include real passwords, keys or links to instances that hold other people's data. If you need to share a proof of concept, use an instance you created yourself.

## What happens next

| When | What |
|---|---|
| Within 3 working days | Your report is acknowledged. |
| Within 10 working days | You receive an assessment: accepted, needs more information, or not a vulnerability. |
| When a fix is ready | It is published, the advisory is made public, and you are credited unless you prefer not to be. |

Please give a reasonable time to fix the problem before you talk about it publicly.

## Scope

In scope: the panel, its gateway service, the release images and the installer (`sudoo-install.sh`), its k3s preparation script (`k3s-prepare.sh`, carried by the installer) and the cluster access it grants the panel (RBAC and the admission policy `sudoo-panel-scope`), and the way they store secrets and run commands.

Out of scope, because they are documented on purpose in [docs/SECURITY.md](docs/SECURITY.md) and the README's *Known Limitations*:

- Odoo instances open with the login `admin` / `admin`; they are for demonstrations, not real data.
- Public addresses are public to anyone who has them; with `EXPOSURE=tunnel` they change on restart and https ends at the tunnel service's edge; with `EXPOSURE=direct` and no name set, the panel uses its own certificate, which browsers warn about.
- The vault key is on the same server as the secrets it protects.
- Production access rules (beta) by country: a VPN or a server in an allowed country gets past them; they are a convenience barrier (address rules are stronger).
- Problems in Odoo itself, Docker, PostgreSQL, the certificate authority or the tunnel service. Please report those to their own projects.

The full description of how the panel is protected, and what is deliberately not protected, is in [docs/SECURITY.md](docs/SECURITY.md).
