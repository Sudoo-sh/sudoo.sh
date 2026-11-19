"""The folders another container can also write (GATEWAY_DIR for the gateway, MAIL_CATCHER_DIR for the mail
catcher): a link planted there never makes the panel write, change, read or remove anything outside them."""

from __future__ import annotations

import os
import shutil
import socket
import stat
import time
from pathlib import Path

import pytest

from sudoo.config import settings
from sudoo.core import vault
from sudoo.instances import certificates, gateway_link
from sudoo.instances.gateway_service import ROUTES, path
from sudoo.mailcatcher import mailbox
from sudoo.mailcatcher import panel as catcher


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A file of the panel's own that only a followed link could reach."""
    victim = tmp_path / "panel-only" / "vault.key"
    victim.parent.mkdir()
    victim.write_bytes(b"ORIGINAL")
    return victim


@pytest.fixture(autouse=True)
def _clean_folders() -> None:
    for folder in (settings.gateway_dir, settings.mail_catcher_dir):
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, mode=0o700)


def test_write_private_replaces_a_link_at_the_name_and_ignores_one_at_the_old_temporary_name(outside: Path) -> None:
    target = settings.gateway_dir / "routes.json"
    (settings.gateway_dir / "routes.json.tmp").symlink_to(outside)
    target.symlink_to(outside)
    vault.write_private(target, '{"format": 1}', within=settings.gateway_dir)
    assert outside.read_bytes() == b"ORIGINAL"
    assert not target.is_symlink() and target.read_text() == '{"format": 1}'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [p.name for p in settings.gateway_dir.iterdir() if p.name.startswith(".")] == []  # no temporary left


def test_routes_json_never_lands_in_the_vault_key(outside: Path) -> None:
    (settings.gateway_dir / "routes.json.tmp").symlink_to(outside)
    gateway_link.LINK.written = None
    gateway_link.write_routes('{"format": 1, "planted": true}')
    assert outside.read_bytes() == b"ORIGINAL"
    assert not path(ROUTES).is_symlink() and "planted" in path(ROUTES).read_text()


def test_a_certificate_is_never_written_through_a_planted_folder_or_file(outside: Path) -> None:
    certs = settings.gateway_dir / certificates.CERTS
    certs.mkdir(mode=0o700)
    (certs / "shop.example.com.pem.tmp").symlink_to(outside)
    (certs / "shop.example.com.pem").symlink_to(outside)
    vault.write_private(certificates.cert_file("shop.example.com"), "KEY+CHAIN", within=settings.gateway_dir)
    assert outside.read_bytes() == b"ORIGINAL"
    assert certificates.cert_file("shop.example.com").read_text() == "KEY+CHAIN"
    # the whole folder swapped for a link to the panel's own folder: the write fails, nothing lands there
    shutil.rmtree(certs)
    certs.symlink_to(outside.parent, target_is_directory=True)
    with pytest.raises(OSError):
        vault.write_private(certificates.cert_file("shop.example.com"), "KEY+CHAIN", within=settings.gateway_dir)
    assert sorted(p.name for p in outside.parent.iterdir()) == ["vault.key"]
    (outside.parent / "shop.example.com.pem").write_text("the panel's own")
    certificates.remove("shop.example.com")  # removing a certificate does not reach through the link either
    assert (outside.parent / "shop.example.com.pem").exists()


def test_the_certificate_folders_are_made_private_without_following_a_link(outside: Path) -> None:
    os.chmod(outside.parent, 0o755)
    (settings.gateway_dir / certificates.CHALLENGES).symlink_to(outside.parent, target_is_directory=True)
    found = certificates.challenges_dir()
    assert found.is_dir() and not found.is_symlink()  # the link is replaced by a real folder
    assert stat.S_IMODE(outside.parent.stat().st_mode) == 0o755
    certificates.publish_challenge("Tok_en-1abcdefghijklmnopqrstuv", "answer")
    assert (found / "Tok_en-1abcdefghijklmnopqrstuv").read_text() == "answer" and not (
        outside.parent / "Tok_en-1abcdefghijklmnopqrstuv"
    ).exists()


def test_chmod_private_refuses_a_link(outside: Path, tmp_path: Path) -> None:
    os.chmod(outside, 0o644)
    link = settings.gateway_dir / "panel.sock"
    link.symlink_to(outside)
    with pytest.raises(OSError):
        vault.chmod_private(link)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644
    link.unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(link))
        os.chmod(link, 0o666)
        vault.chmod_private(link)
        assert stat.S_IMODE(os.lstat(link).st_mode) == 0o600


def test_the_catcher_settings_never_land_through_a_link(outside: Path) -> None:
    (settings.mail_catcher_dir / catcher.SETTINGS_FILE).symlink_to(outside)
    (settings.mail_catcher_dir / f"{catcher.SETTINGS_FILE}.tmp").symlink_to(outside)
    assert catcher.write_settings()
    assert outside.read_bytes() == b"ORIGINAL"
    assert not (settings.mail_catcher_dir / catcher.SETTINGS_FILE).is_symlink()


def test_the_panel_reads_and_removes_caught_mail_only_inside_the_mailbox(outside: Path) -> None:
    box = mailbox.root() / "mc-dev"
    box.mkdir(parents=True)
    message_id = mailbox.new_id()
    (box / f"{message_id}.eml").symlink_to(outside)
    (box / f"{message_id}.json").write_text('{"subject": "planted"}')
    assert mailbox.raw("mc-dev", message_id) is None  # the panel file is never shown or released
    os.mkfifo(box / f"{mailbox.new_id(time.time() + 1)}.json")
    assert [found["subject"] for found in mailbox.summaries("mc-dev")] == ["planted"]  # a pipe does not block
    old = time.time() - 7200
    hidden = outside.parent / ".hidden"
    hidden.write_text("kept")
    os.utime(hidden, (old, old))
    mailbox.delete("mc-dev", message_id)
    mailbox.prune("mc-dev")
    assert outside.read_bytes() == b"ORIGINAL"
    # a mailbox swapped for a link to the panel's folder: nothing there is read, pruned or removed
    shutil.rmtree(box)
    box.symlink_to(outside.parent, target_is_directory=True)
    assert mailbox.usage("mc-dev") == {"messages": 0, "bytes": 0}
    mailbox.prune("mc-dev")
    mailbox.remove_box("mc-dev")
    assert outside.read_bytes() == b"ORIGINAL" and hidden.exists()
    # the folder of all mailboxes swapped for a link: removing a mailbox does not reach through it
    box.unlink()
    shutil.rmtree(mailbox.root())
    mailbox.root().symlink_to(outside.parent.parent, target_is_directory=True)
    mailbox.remove_box(outside.parent.name)
    assert outside.read_bytes() == b"ORIGINAL"
