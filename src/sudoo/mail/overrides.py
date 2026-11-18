"""The texts changed on the E-mail templates page, kept in the panel's storage (collection "mail_templates").

One document per e-mail (catalog.MESSAGES): {"subject", "title", "body", "changed", "changed_by"}. The built-in
texts stay in catalog.py; resetting an e-mail deletes its document.
"""

from __future__ import annotations

import time
from typing import Any

from sudoo.storage import storage

COLLECTION = "mail_templates"


def get(key: str) -> dict[str, Any] | None:
    return storage().get(COLLECTION, key)


def save(key: str, subject: str, title: str, body: str, by: str) -> dict[str, Any]:
    document = {"subject": subject, "title": title, "body": body, "changed": time.time(), "changed_by": by}
    storage().put(COLLECTION, key, document)
    return document


def reset(key: str) -> bool:
    """Forget the change; True when there was one."""
    return storage().delete(COLLECTION, key)
