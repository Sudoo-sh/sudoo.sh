# Windows servers

**Audience:** administrators who run Windows Server, or Windows 11, and want to host Sudoo.sh there.
**Covers:** why Sudoo.sh needs Linux, the ways Windows can run Linux, a proposed installer, its limits and the effort.
**Status:** planned, not supported yet. Install Sudoo.sh on a Linux server or virtual machine (Ubuntu 24.04 LTS or
Debian 12) today.

---

## Contents

1. [Why Linux](#1-why-linux)
2. [The ways Windows runs Linux](#2-the-ways-windows-runs-linux)
3. [The proposed approach](#3-the-proposed-approach)
4. [Limits](#4-limits)
5. [Effort](#5-effort)

---

## 1. Why Linux

Odoo, PostgreSQL, the panel (`sudoo-panel`, `sudoo-gateway`, `sudoo-mail`) and the web service (`sudoo-web`) run as
**Linux containers**. Their images exist for linux/amd64 and linux/arm64 only; there are no Windows container images
of Odoo or PostgreSQL, and the panel drives the Docker Engine of a Linux host through its socket. Both engines need
Linux:

- **Docker:** the Docker Engine that runs Linux containers runs on a Linux kernel. On Windows that kernel comes from
  WSL 2 or a Hyper-V virtual machine.
- **Kubernetes (k3s, beta):** k3s runs its server (control plane) on Linux only. Windows can only be a worker for
  Windows containers in some Kubernetes distributions, and Odoo has none, so a Windows machine cannot be a k3s server
  role for Sudoo.sh.

## 2. The ways Windows runs Linux

| Way | Where | What it means for Sudoo.sh |
| :--- | :--- | :--- |
| **Hyper-V virtual machine** | Windows Server 2016+ (Standard, Datacenter), Windows 10/11 Pro and Enterprise | A full Ubuntu server: the normal installer runs unchanged, with its own systemd, network address, firewall and disk. The most faithful option. |
| **WSL 2** | Windows 11, Windows Server 2025 (and 2022 with updates) | A light Linux VM managed by Windows. systemd can be turned on (`/etc/wsl.conf`). Networking is NAT by default (mirrored mode on recent builds); the distribution stops when nobody uses it unless kept running, and its disk is a growing VHDX. Workable, with care. |
| **Docker Desktop** | Windows 10/11 | Runs Linux containers through WSL 2, but is a desktop product: its licence requires a paid subscription for larger companies, and Docker does not support it on Windows Server. Not a basis for a server. |
| **Mirantis Container Runtime / Windows containers** | Windows Server | Runs Windows containers. No Odoo or PostgreSQL images exist for them. Not usable. |

## 3. The proposed approach

A small PowerShell installer, `sudoo-install.ps1`, signed like the Linux installer, that prepares Linux and then runs
the normal installer inside it:

1. **Check the machine:** Windows edition and build, virtualisation turned on in the firmware, free memory (at least
   4 GB for the Linux side, 8 GB recommended) and disk, ports 80 and 443 free on Windows.
2. **Choose the way:**
   - **Hyper-V (recommended on Windows Server):** turn on the Hyper-V role (asks first; needs a restart), download the
     Ubuntu 24.04 cloud image and check its SHA-256, create a Generation 2 VM (secure boot with the Microsoft UEFI CA,
     2+ processors, memory as chosen, a 60 GB+ dynamic disk), give it cloud-init settings (a user, an SSH key, the
     time zone), attach it to an external virtual switch so it has its own address on the network, and start it.
   - **WSL 2 (Windows 11, Windows Server 2025):** install WSL and Ubuntu 24.04, turn on systemd, choose mirrored
     networking where available, and register a scheduled task that starts the distribution at boot and keeps it
     running.
3. **Run the normal installer inside**: `curl -fsSL https://get.sudoo.sh | sudo bash`, with the same licence step
   (typed `accept` or `--accept-licence`; never accepted on the person's behalf) and the same engine choice.
4. **Forward ports and open the firewall** (WSL 2 with NAT only): `netsh interface portproxy` for 80 and 443, and
   Windows Defender Firewall rules for them; Hyper-V with an external switch needs neither.
5. **Print the setup link** from the Linux side, as the Linux installer does, and add a Start-menu shortcut to it.

`sudoo-install.ps1 status` and `update` would run the Linux installer's commands inside the VM or distribution;
`uninstall` would offer to remove the VM or distribution after the panel's own uninstall.

## 4. Limits

- **Performance:** a VM adds a little overhead; WSL 2's file access across the Windows and Linux file systems is
  slow, so everything stays inside the Linux disk (`/opt/odoo`), never on `C:\`.
- **Memory:** WSL 2 takes memory as it needs it and gives it back late; a fixed limit in `.wslconfig` is needed so
  Windows keeps enough.
- **Networking:** with WSL 2's NAT the Linux address changes at restarts and port forwarding must follow it; mirrored
  networking avoids that but is only on recent Windows builds. Hyper-V with an external switch behaves like any server.
- **Backups and updates:** Windows Update restarts stop the VM or distribution; it must start again at boot and the
  panel's own restart policies bring the containers back. Backups of the Windows side do not include the Linux disk
  consistently while it runs; Sudoo.sh's own backups (and off-site copies) are the ones to rely on.
- **Kubernetes:** k3s runs inside the Linux VM only (Hyper-V). Under WSL 2 it is not recommended.
- **Support:** Windows itself, Hyper-V and WSL are outside what Sudoo.sh can diagnose from the panel.

## 5. Effort

| Part | Estimate |
| :--- | :--- |
| PowerShell installer: checks, Hyper-V VM with cloud-init, WSL 2 path, port forwarding, firewall | 2-3 weeks |
| Signing (Authenticode and Sigstore), release workflow, `get.sudoo.sh` for Windows | 3-4 days |
| Tests: Pester unit tests, a Windows Server 2022/2025 and Windows 11 matrix on CI runners with nested virtualisation | 1-2 weeks |
| Documentation (this page becomes a guide), troubleshooting | 3-4 days |
| **Total** | **about 5-7 weeks** for one person, then ongoing testing on new Windows builds |

Until then, the simplest way on a Windows machine is to create an Ubuntu 24.04 VM in Hyper-V by hand and run the
normal installer in it (docs/ADMIN_GUIDE.md, section 2).
