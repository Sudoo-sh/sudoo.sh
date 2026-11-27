"""Hide likely secrets and personal details in the lines of a failed step before anything is shown for sending.

This reduces what leaves; it cannot guarantee that no secret is included. It works on text that failure.clean() has
already cleaned (server paths, addresses, GitHub-style tokens, container names), and adds what clean() does not hide:
Authorization and Bearer values, name=value pairs whose name looks secret (password, secret, token, key...), command
line flags with a secret, addresses with a user name and password of any scheme, private-key blocks, JWTs, well
known key shapes, long random-looking strings, e-mail addresses and IPv6 addresses.

Wrapping is handled only for common cases (a private key block, a credential whose value continues on the next
line, runs of long token lines). Known limits, stated on the screen and in the docs: a secret broken over two lines
in any other way, a secret with no recognisable
name or shape (a plain word after "is"), a personal name or a company name in a message, and text in a language the
patterns do not know are NOT hidden. The person who confirms sees the result before it is sent.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable

from sudoo.core.failure import clean

HIDDEN = "<hidden>"

_PEM = re.compile(
    r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE|PGP[A-Z ]*BLOCK)-----.*?(?:-----END [A-Z0-9 ]*-----|\Z)", re.S
)
_AUTH = re.compile(
    r"(?i)\b(authorization|proxy-authorization)(\"?\s*[:=]\s*\"?)(?:(bearer|basic|token|digest)\s+)?[^\s\"',;]+"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_NAME = (
    r"[\w.-]*(?:pass(?:word|wd|phrase)?|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|"
    r"credential|auth|signature|cookie|session[_-]?id|dsn|connection[_-]?string|(?<![a-z])key(?![a-z]))[\w.-]*"
)
_PAIR = re.compile(
    rf"(?i)(?<![\w-])((?:--)?{_NAME})([\"']?\s*[:=]\s*)(\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|[^\n]+)"
)  # a quoted value may hold a backslash-escaped quote; an unclosed quote falls back to the rest of the line
_FLAG = re.compile(
    r"(?i)(--[\w-]*(?:pass(?:word|wd)?|token|secret|api[_-]?key|access[_-]?key|auth)[\w-]*\s+)(\"[^\"]*\"|'[^']*'|\S+)"
)
_USERINFO = re.compile(
    r"\b([a-zA-Z][a-zA-Z0-9+.-]*://)"
    r"(?:[^\s/]*@"  # everything up to the last "@" before the first "/" (a password may hold ":", "@", "?", "#")
    r"|[^\s/@:]*:(?!\d{1,5}(?:/|\s|$))[^\s]*@)"  # a password with a "/" in it: up to the last "@" of the word, unless
)  # what follows the first ":" is a port (host:8080/path?x=a@b is an address, not a credential)
_CLI_USER = re.compile(r"(?i)(\s(?:-u|--user)(?:\s+|=))[^\s:]+:\S+")  # curl -u user:pass
_CLI_P = re.compile(r"(?<=\s)-p\S+")  # mysql -pPASSWORD (the value is stuck to the flag)
_IS = re.compile(r"(?i)\b(pass(?:word|wd|phrase)|secret|token|api key|private key)\b([^\n]{0,60}?\b(?:is|was)\s+)(\S+)")
_WORD_DIGIT = re.compile(r"(?i)\b(token|secret|password|passwd)(\s+)(?=\S*\d)\S+")  # "token hunter2"
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{3,}")
_KNOWN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sudoo_[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9_-]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}|(?:AKIA|ASIA)[0-9A-Z]{16}|glpat-[A-Za-z0-9_-]{20,}|"
    r"npm_[A-Za-z0-9]{30,}|pypi-[A-Za-z0-9_-]{30,})\b"
)
_HEX = re.compile(r"(?<![\w/.-])[0-9a-fA-F]{32,}(?![\w/.-])")
_RANDOM = re.compile(r"(?<![\w/.+-])[A-Za-z0-9+/_-]{40,}={0,2}(?![\w/.+-])")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_V6 = re.compile(r"(?<![\w:])[0-9A-Fa-f:]{3,45}(?![\w:])")
_V4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


_TOKEN_LINE = re.compile(r"^\s*[A-Za-z0-9+/_=.-]{8,}\s*$")
_LONG_TOKEN_LINE = re.compile(r"^\s*[A-Za-z0-9+/_=-]{20,}\s*$")
_ENDS_WITH_SECRET = re.compile(rf"(?i)(?<![\w-])(?:--)?{_NAME}[\"']?\s*[:=]\s*\S+$")
_ENDS_WITH_LONG_TOKEN = re.compile(r"(?:^|\s)[A-Za-z0-9+/_=-]{20,}\s*$")
_ENDS_WITH_BEARER = re.compile(r"(?i)\b(?:bearer|basic)\s*$")


def _join_wrapped(text: str) -> str:
    """Best-effort: a credential that a tool wrapped onto the next line(s), as one line, so the patterns see it whole.
    Joins a line that ends with a secret-looking name=value, or with Bearer/Basic, to the token-only line after it,
    and runs of long token-only lines (a wrapped key or hash) to each other. Other wrapping is not recognised."""
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        if out and _TOKEN_LINE.match(line):
            previous = out[-1]
            if _ENDS_WITH_BEARER.search(previous):
                out[-1] = f"{previous} {line.strip()}"
                continue
            if _ENDS_WITH_SECRET.search(previous):
                out[-1] = previous.rstrip().rstrip("\"'") + line.strip()
                continue
            if _ENDS_WITH_LONG_TOKEN.search(previous) and len(line.strip()) >= 12:
                out[-1] = previous.rstrip() + line.strip()
                continue
        out.append(line)
    return "\n".join(out)


def _random(match: re.Match[str]) -> str:
    text = match.group(0)
    # a long identifier made of letters only, or of lower-case words with dashes, is not a secret; a mix is
    mixed = sum(bool(re.search(p, text)) for p in (r"[a-z]", r"[A-Z]", r"\d")) >= 2
    return HIDDEN if mixed and not text.isalpha() else text


def _v6(match: re.Match[str]) -> str:
    text = match.group(0)
    if text.count(":") < 2:
        return text
    try:
        ipaddress.IPv6Address(text)
    except ValueError:
        return text
    return "<address>"


def scrub(text: str) -> tuple[str, int]:
    """(`text` with likely secrets hidden, how many places were changed). `text` is lines of a failed step."""
    cleaned = "\n".join(clean(line) for line in text.splitlines())
    count = 0

    def sub(pattern: re.Pattern[str], repl: str | Callable[[re.Match[str]], str], value: str) -> str:
        nonlocal count
        value, n = pattern.subn(repl, value)
        count += n
        return value

    out = sub(_PEM, HIDDEN, _join_wrapped(cleaned))
    out = sub(_AUTH, lambda m: f"{m.group(1)}{m.group(2)}{HIDDEN}", out)
    out = sub(_BEARER, lambda m: f"{m.group(1)} {HIDDEN}", out)
    out = sub(_USERINFO, lambda m: f"{m.group(1)}{HIDDEN}@", out)
    out = sub(_PAIR, lambda m: f"{m.group(1)}{m.group(2)}{HIDDEN}", out)
    out = sub(_FLAG, lambda m: f"{m.group(1)}{HIDDEN}", out)
    out = sub(_CLI_USER, lambda m: f"{m.group(1)}{HIDDEN}", out)
    out = sub(_CLI_P, "-p" + HIDDEN, out)
    out = sub(_IS, lambda m: f"{m.group(1)}{m.group(2)}{HIDDEN}", out)
    out = sub(_WORD_DIGIT, lambda m: f"{m.group(1)}{m.group(2)}{HIDDEN}", out)
    out = sub(_JWT, HIDDEN, out)
    out = sub(_KNOWN, HIDDEN, out)
    out = sub(_HEX, HIDDEN, out)
    before = out
    out = _RANDOM.sub(_random, out)
    count += int(out != before) and out.count(HIDDEN) - before.count(HIDDEN)
    out = sub(_EMAIL, "<email>", out)
    before = out
    out = _V6.sub(_v6, out)
    count += out.count("<address>") - before.count("<address>")
    out = sub(_V4, "<address>", out)
    return out, count
