"""The mail catcher: staging and development instances never send real e-mail.

Odoo.sh catches the e-mail of its development and staging branches; Sudoo.sh does the same with one small SMTP
service, the sudoo-mail container (`sudoo.sh mailcatcher`, server.py). It runs the panel's own image without the
Docker socket, without `.env` and without a published port, read-only, every capability dropped, and reads and
writes only MAIL_CATCHER_DIR (<ODOO_ROOT>/mail), which it shares with the panel:

    catcher.json      written by the panel (panel.py): the limits (largest e-mail, e-mails and space per instance)
                      and one mailbox per staging or development instance, with the SHA-256 of its SMTP password.
                      Read again whenever it changes.
    boxes/<instance>/ written here: each caught e-mail as <id>.eml (the message as received) and <id>.json (its
                      envelope and a summary: subject, addresses, size, attachments). The panel reads these files
                      directly for the instance's Mail tab, deletes them, and removes those past the retention.

How e-mail reaches it (panel.py):

    network    the panel connects the container to the Compose network of each staging and development instance
               (odoo-<name>_default, the Odoo container's own; the database is not on it), and disconnects it from
               production instances. The catcher answers on port 2525 as "sudoo-mail" there; it is on no other
               network the instances can reach, and never on the server's own addresses.
    odoo.conf  smtp_server = sudoo-mail, smtp_port = 2525, smtp_user = the instance's name, smtp_password = a secret
               of the instance (.env MAIL_CATCHER_PASSWORD), smtp_ssl = False; production keeps none of these.
    database   every outgoing mail server (ir.mail_server) of a staging or development instance is switched off
               when it starts and every few minutes while it runs, so Odoo falls back to odoo.conf's server: the
               catcher. Restoring another instance's data neutralises it as before (instances/backups.py).

Per-instance isolation: the catcher accepts e-mail only after SMTP authentication, and files it in the mailbox of
the instance whose password was given; an instance knows only its own password, so it cannot write into another
instance's mailbox, and the panel shows each instance only its own mailbox.

Releasing (panel.release) sends a copy of a caught e-mail through the panel's own mailer (MAILER), from the panel's
address, to one address an administrator types; it is audit-logged and can be switched off (MAIL_CATCHER_RELEASE).
"""

from __future__ import annotations

HOST = "sudoo-mail"
"""The catcher's container name, and the name Odoo reaches it by on the instances' networks."""

PORT = 2525
SETTINGS_FILE = "catcher.json"
BOXES = "boxes"
FORMAT = 1
