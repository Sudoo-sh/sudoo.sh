"""Checking every value that comes from a browser or an API client before it is used.

Rules applied to all input (see docs/SECURITY.md):
    - a request that changes something must send a JSON object with Content-Type application/json
      (checked in security.py); a form posted from another site cannot do that
    - a value has the expected type: text is text, a switch is true or false, a number is a number
    - text is trimmed, limited in length and may not contain control characters (no NUL bytes, no new lines)
    - names, addresses and branches must match an allow-list pattern; nothing is "cleaned up" silently
Values are then only ever passed to commands as separate arguments, never through a shell (shell.py), and only
ever shown in the browser escaped (static/js/core.js, esc()).

Every check stops the request with 400 and a message that says what to enter instead.
"""
import re

from flask import abort, request

from .. import accounts, backups, github, instances

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MODULES_RE = re.compile(r"^[a-z0-9_]{1,64}(,[a-z0-9_]{1,64}){0,99}$")
SUBMODULE_PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]{0,100}$")
CUSTOM_NAME_RE = re.compile(r"^[A-Za-z0-9 ._-]{1,40}$")


def body():
    """The request's JSON object (security.py has already checked the Content-Type)."""
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        abort(400, "The request must be a JSON object.")
    return data


def text(data, key, *, max_length=200, default=""):
    """A trimmed text field; numbers are refused, missing or null gives `default`."""
    value = data.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        abort(400, f"The field '{key}' must be text.")
    value = value.strip()
    if len(value) > max_length:
        abort(400, f"The field '{key}' is too long (at most {max_length} characters).")
    if _CONTROL.search(value):
        abort(400, f"The field '{key}' contains characters that are not allowed.")
    return value


def secret(data, key):
    """A password field: kept exactly as typed (spaces count), limited in length, never logged."""
    value = data.get(key)
    if not isinstance(value, str) or not value:
        abort(400, "Enter the password.")
    if len(value) > accounts.MAX_PASSWORD_LENGTH:
        abort(400, f"The password must be at most {accounts.MAX_PASSWORD_LENGTH} characters long.")
    return value


def flag(data, key):
    """A switch: true or false; missing means false."""
    value = data.get(key, False)
    if not isinstance(value, bool):
        abort(400, f"The field '{key}' must be true or false.")
    return value


def days(data, key, maximum=3650):
    """A number of days sent as a JSON number: 0 (meaning "never") up to `maximum`."""
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
        abort(400, f"The field '{key}' must be a whole number of days between 0 and {maximum}.")
    return value


def backup_file(value):
    """The name of an existing backup file (never a path)."""
    path = backups.path_of(value)
    if path is None:
        abort(404, "This backup does not exist.")
    return path


def whole_number(value, name, minimum=0, maximum=10 ** 9):
    """A whole number given as text (query parameters), within limits."""
    if not re.fullmatch(r"\d{1,10}", value or ""):
        abort(400, f"'{name}' must be a whole number.")
    number = int(value)
    if not minimum <= number <= maximum:
        abort(400, f"'{name}' must be between {minimum} and {maximum}.")
    return number


# --------------------------------------------------------------------------- domain values

def one_of(value, allowed, message):
    if value not in allowed:
        abort(400, message)
    return value


def instance_name(value):
    if not instances.NAME_RE.match(value or ""):
        abort(404, "This instance does not exist.")
    return value


def custom_name(value):
    """The optional name typed when creating an instance (turned into a technical name afterwards)."""
    if value and not CUSTOM_NAME_RE.match(value):
        abort(400, "The name may contain letters, numbers, spaces, dots, dashes and underscores (at most 40).")
    return value


def repo_address(value, required=True):
    if not value and not required:
        return value
    if not github.ADDRESS_RE.match(value) or not github.repo_name(value):
        abort(400, "This is not a GitHub repository address. Example: https://github.com/company/repository")
    return value


def repo_reference(value):
    """A repository given as a GitHub address or as owner/repository."""
    if not github.repo_name(value):
        abort(400, "Enter the repository as owner/repository or as its GitHub address.")
    return value


def branch(value, required=True):
    if not value and not required:
        return value
    if not github.valid_branch(value):
        abort(400, "This branch name is not valid.")
    return value


def module_list(value):
    modules = value.replace(" ", "").strip(",")
    if not MODULES_RE.match(modules):
        abort(400, "Enter technical module names separated by commas, for example: sale_custom,stock_custom")
    return modules.split(",")


def submodule_path(value):
    path = value.strip("/")
    if not SUBMODULE_PATH_RE.match(path) or ".." in path.split("/") or "//" in path:
        abort(400, "The folder name may only contain letters, numbers, dashes, dots and slashes.")
    return path


def username(value):
    if not accounts.USERNAME_RE.match(value):
        abort(400, "User names are 3 to 32 characters: lower-case letters, digits, dots, dashes and underscores, "
                   "starting with a letter.")
    return value
