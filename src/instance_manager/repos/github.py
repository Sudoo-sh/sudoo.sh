"""GitHub access: one read-only deploy key per private repository, and one way to fetch each repository.

How git reaches a GitHub repository (configured once for every git command the panel runs):
    repository with a deploy key   over SSH, with that repository's key   (git_ssh.py picks the key)
    any other repository           over anonymous HTTPS                   (public repositories)

A deploy key works for exactly one repository, so the server keeps one key per repository, shared by all
instances that use it. Repository addresses may be written as https://github.com/... or git@github.com:...;
both are mapped to the one way above.
"""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from instance_manager.config import settings
from instance_manager.core import vault
from instance_manager.core.hints import friendly_hint
from instance_manager.core.shell import capture
from instance_manager.repos.gitrepo import git

REPO_ID_RE = re.compile(r"^[a-z0-9_.-]+/[a-z0-9_.-]+$")
ADDRESS_RE = re.compile(r"^(https://github\.com/|git@github\.com:)[\w.-]+/[\w.-]+/?$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]{0,99}$")  # never starts with "-", so never read as an option


def valid_branch(name: str) -> bool:
    """True for a branch name git accepts and that cannot be mistaken for a command option or a path trick."""
    return (
        bool(BRANCH_RE.match(name or ""))
        and ".." not in name
        and "//" not in name
        and not name.endswith(("/", ".", ".lock"))
    )


def repo_name(address: str) -> str | None:
    """'Owner/Repo' as written in a GitHub address or 'owner/repo' text, else None."""
    match = re.match(r"^(?:https://github\.com/|git@github\.com:)(.+?)(?:\.git)?/?$", address.strip()) or re.match(
        r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$", address.strip()
    )
    name = match.group(1) if match else ""
    return name if REPO_ID_RE.match(name.lower()) and ".." not in name else None


def repo_id(address: str) -> str | None:
    """'owner/repo' in lower case: GitHub repository names are not case-sensitive, keys are stored per id."""
    name = repo_name(address)
    return name.lower() if name else None


# --------------------------------------------------------------------------- deploy keys


def key_file(rid: str, extension: str = ".key") -> Path:
    """Key files of a repository <owner>__<repo>: .key (private key, encrypted), .pub (public key), .name (spelling)."""
    return settings.deploy_keys_dir / (rid.replace("/", "__") + extension)


def key_label(rid: str) -> str:
    """Vault label of a repository's private key; git_ssh.py decrypts it with the same label."""
    return f"deploy-key:{rid}"


def has_key(rid: str | None) -> bool:
    return bool(rid and key_file(rid, ".key").exists())


def create_key(address: str) -> tuple[str, str]:
    """Create the key pair of a repository (once) and return (repo id, public key).

    ssh-keygen writes the pair into a memory-backed temporary folder; the private key is stored encrypted with the
    vault key and the temporary copy is deleted, so it is never on disk in readable form. The spelling of the
    repository name is kept next to the key, because git's address mapping is case-sensitive.
    """
    rid, spelling = repo_id(address), repo_name(address)
    settings.deploy_keys_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    assert rid is not None and spelling is not None  # callers pass a validated repository address
    if not has_key(rid):
        with tempfile.TemporaryDirectory(dir=settings.memory_dir) as tmp:
            plain = Path(tmp) / "key"
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"oim-panel {rid}", "-f", str(plain)], check=True
            )
            vault.write_private(key_file(rid, ".key"), vault.encrypt(plain.read_bytes(), key_label(rid)))
            key_file(rid, ".pub").write_text(plain.with_suffix(".pub").read_text())
    key_file(rid, ".name").write_text(spelling)
    configure_git()
    return rid, public_key(rid)


def delete_key(rid: str) -> None:
    for extension in (".key", ".pub", ".name"):
        key_file(rid, extension).unlink(missing_ok=True)
    configure_git()


def public_key(rid: str) -> str:
    return key_file(rid, ".pub").read_text().strip()


def add_key_url(rid: str) -> str:
    """GitHub page where the key is added."""
    return f"https://github.com/{rid}/settings/keys/new"


def keys() -> list[dict[str, Any]]:
    """All deploy keys: [{"repo", "public_key", "created", "github_url"}]."""
    if not settings.deploy_keys_dir.exists():
        return []
    result: list[dict[str, Any]] = []
    for pub in sorted(settings.deploy_keys_dir.glob("*.pub")):
        rid = pub.name[: -len(".pub")].replace("__", "/")
        spelling = key_file(rid, ".name").read_text().strip()
        result.append(
            {
                "repo": rid,
                "spelling": spelling,
                "public_key": pub.read_text().strip(),
                "created": pub.stat().st_mtime,
                "github_url": add_key_url(rid),
            }
        )
    return result


# --------------------------------------------------------------------------- git configuration


def configure_git() -> None:
    """Set the environment every git command inherits: identity, key selection and address mapping.

    git applies the longest matching `insteadOf` prefix, so the per-repository rules (with a deploy key) win over
    the general rule that sends every other GitHub repository to anonymous HTTPS.
    """
    os.environ["GIT_SSH_COMMAND"] = f"{sys.executable} -m instance_manager.repos.git_ssh"
    os.environ["GIT_SSH_VARIANT"] = "ssh"
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        os.environ[var] = "Odoo Instance Manager"
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        os.environ[var] = "oim@localhost"

    rules = [("url.https://github.com/.insteadOf", "git@github.com:")]
    key: Any  # a key record in the first loop, a config key name in the last one
    for key in keys():
        for name in {key["repo"], key["spelling"]}:
            ssh = f"git@github.com:{name}"
            rules += [(f"url.{ssh}.insteadOf", f"https://github.com/{name}"), (f"url.{ssh}.insteadOf", ssh)]
    for name in [k for k in os.environ if k.startswith("GIT_CONFIG_")]:
        del os.environ[name]
    os.environ["GIT_CONFIG_COUNT"] = str(len(rules))
    for i, (key, value) in enumerate(rules):
        os.environ[f"GIT_CONFIG_KEY_{i}"] = key
        os.environ[f"GIT_CONFIG_VALUE_{i}"] = value


# --------------------------------------------------------------------------- repository checks


def branches(address: str) -> tuple[list[str], str | None, str | None]:
    """(branches, default branch, error) of a repository."""
    result = subprocess.run(["git", "ls-remote", "--symref", "--", address], capture_output=True, text=True, timeout=40)
    if result.returncode != 0:
        return [], None, friendly_hint(result.stderr)
    names: list[str] = []
    default: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            default = line[len("ref: refs/heads/") :].split("\t")[0]
        elif "\trefs/heads/" in line:
            names.append(line.split("\trefs/heads/", 1)[1])
    return sorted(names), default, None


def _readable(address: str) -> tuple[bool, str]:
    result = subprocess.run(["git", "ls-remote", "--heads", "--", address], capture_output=True, text=True, timeout=40)
    return result.returncode == 0, ("" if result.returncode == 0 else friendly_hint(result.stderr))


def _absolute(parent: str, address: str) -> str:
    """Resolve a submodule address that is relative to its repository (e.g. ../other.git)."""
    if not address.startswith(("../", "./")):
        return address
    match = re.match(r"^(git@[^:]+:|https://[^/]+/)(.*)$", parent)
    assert match is not None
    prefix, path = match.groups()
    return prefix + posixpath.normpath(posixpath.join(path.rstrip("/"), address)).lstrip("/")


def access_report(address: str, branch: str = "") -> list[dict[str, Any]]:
    """Can the server read the repository and every submodule of `branch` (default branch when empty)?

    One entry per repository: {"url", "role", "ok", "problem", "repo", "has_key", "github_url"}.
    """
    readable, problem = _readable(address)
    report: list[dict[str, Any]] = [{"url": address, "role": "main", "ok": readable, "problem": problem}]
    if readable:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "-q",
                    "--depth",
                    "1",
                    "--no-checkout",
                    "--filter=blob:none",
                    *(["-b", branch] if branch else []),
                    "--",
                    address,
                    tmp,
                ],
                capture_output=True,
                timeout=120,
            )
            submodule_lines = capture(git(tmp, "config", "--blob", "HEAD:.gitmodules", "--get-regexp", r"\.url$"))
        for line in submodule_lines.splitlines():
            sub_address = _absolute(address, line.split(" ", 1)[1].strip())
            ok, sub_problem = _readable(sub_address)
            report.append({"url": sub_address, "role": "submodule", "ok": ok, "problem": sub_problem})
    for entry in report:
        rid = repo_id(entry["url"])
        entry.update(repo=rid, has_key=has_key(rid), github_url=add_key_url(rid) if rid else None)
    return report
