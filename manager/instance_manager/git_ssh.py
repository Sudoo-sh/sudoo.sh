"""SSH command for git (GIT_SSH_COMMAND): connects with the deploy key of the repository being fetched.

git runs:  python3 git_ssh.py [options] git@github.com "git-upload-pack 'owner/repo.git'"
The repository is read from the last argument. Only repositories with a deploy key are fetched over SSH (see
github.configure_git), so a missing key is an error, reported in the job log.

The private key is stored encrypted (vault.py). For the length of one SSH connection it is decrypted into a
memory-backed temporary file readable by root only, then deleted.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # git starts this file as a script

from instance_manager import github, settings, vault  # noqa: E402


def repository(remote_command):
    """'owner/repo' (lower case) from git's remote command."""
    match = re.search(r"'/?([^']+?)(?:\.git)?/?'", remote_command)
    return match.group(1).lower() if match else "(unknown)"


def main(args):
    rid = repository(args[-1])
    if not github.has_key(rid):
        sys.exit(f"no deploy key for {rid}")
    private_key = vault.decrypt_bytes(github.key_file(rid).read_text(), github.key_label(rid))
    with tempfile.NamedTemporaryFile(dir=settings.MEMORY_DIR) as key:    # created with mode 600
        key.write(private_key)
        key.flush()
        return subprocess.call(["ssh", "-i", key.name, "-o", "IdentitiesOnly=yes",
                                "-o", f"UserKnownHostsFile={settings.KEYS_DIR / 'known_hosts'}",
                                "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15", *args])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
