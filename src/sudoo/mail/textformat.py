"""The plain format of e-mail texts, which the E-mail templates page edits: text with placeholders and a little
formatting, turned into the HTML part and the text part of a message. No template code: nothing an administrator
types is ever run, and every value is HTML-escaped.

    {{ name }}            a placeholder, replaced by its value; only the placeholders of that e-mail are accepted
    blank line            a new paragraph; a single line break stays a line break
    **words**             bold
    - item                a bulleted list (one item per line)
    > words               a highlighted note, for warnings
    [label](target)       a link; alone on its line, a button. The target is a link placeholder ({{ link }}) or an
                          address starting with https://, http:// or mailto:
    {{ block }}           a block placeholder (the code box, the details of a sign-in, the list of alerts) stands
                          alone on its line

A line whose placeholders are all empty is left out, so "Seen {{ repeats }} times." disappears when there is
nothing to say. HTML typed in a text shows as text.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from markupsafe import Markup

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_LINK = re.compile(r"\[([^\[\]\n]+)\]\((\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}|[^()\s]+)\)")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_SENTINEL = re.compile("[-]")  # private-use characters stand for placeholders while a line is shaped
_LITERAL_TARGET = re.compile(r"^(https?://[^\s<>\"']+|mailto:[^\s<>\"']+)$")
_FORBIDDEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-]")

MAX_SUBJECT = 200
MAX_TITLE = 120
MAX_BODY = 10_000

TEXT, LINK, BLOCK = "text", "link", "block"


@dataclass(frozen=True, slots=True)
class Block:
    """A block placeholder's value, made by the panel's code: its HTML (already safe) and its text version."""

    html: Markup
    text: str

    def __bool__(self) -> bool:
        return bool(self.text.strip())


@dataclass(frozen=True, slots=True)
class Placeholder:
    name: str
    description: str
    sample: Any
    kind: str = TEXT  # TEXT, LINK (an address) or BLOCK


# ---------------------------------------------------------------- checking what an administrator typed


def problems(
    subject: str, title: str, body: str, placeholders: Mapping[str, Placeholder], required: tuple[str, ...] = ()
) -> list[str]:
    """Everything wrong with an e-mail text, in plain words; empty when it can be saved."""
    found: list[str] = []
    for label, value, limit in (
        ("subject", subject, MAX_SUBJECT),
        ("title", title, MAX_TITLE),
        ("text", body, MAX_BODY),
    ):
        if not value.strip():
            found.append(f"The {label} is empty.")
        if len(value) > limit:
            found.append(f"The {label} is too long (at most {limit} characters).")
        if _FORBIDDEN.search(value):
            found.append(f"The {label} contains characters that are not allowed.")
    for label, value in (("subject", subject), ("title", title)):
        if "\n" in value or "\r" in value:
            found.append(f"The {label} must be one line.")
    unknown: set[str] = set()
    used: set[str] = set()
    for label, value in (("subject", subject), ("title", title), ("text", body)):
        names = PLACEHOLDER.findall(value)
        used.update(names)
        unknown.update(name for name in names if name not in placeholders)
        if "{{" in PLACEHOLDER.sub("", value) or "}}" in PLACEHOLDER.sub("", value):
            found.append(f"The {label} has a placeholder that is not written like {{{{ name }}}}.")
        if label != "text":
            blocks = [name for name in names if name in placeholders and placeholders[name].kind == BLOCK]
            if blocks:
                found.append(f"{{{{ {blocks[0]} }}}} can only be used in the text, alone on its line.")
    if unknown:
        listed = ", ".join(f"{{{{ {name} }}}}" for name in sorted(unknown))
        found.append(f"Unknown placeholder{'s' if len(unknown) > 1 else ''}: {listed}. Use the ones listed.")
    for line in body.splitlines():
        stripped = line.strip()
        for name in PLACEHOLDER.findall(line):
            spec = placeholders.get(name)
            if spec and spec.kind == BLOCK and not PLACEHOLDER.fullmatch(stripped):
                found.append(f"{{{{ {name} }}}} must stand alone on its line.")
        for _label, target in _LINK.findall(line):
            match = PLACEHOLDER.fullmatch(target)
            if match:
                spec = placeholders.get(match.group(1))
                if spec and spec.kind != LINK:
                    found.append(f"{{{{ {spec.name} }}}} is not an address: a link needs a link placeholder.")
            elif not _LITERAL_TARGET.match(target):
                found.append(f"The link to {target[:60]} is not allowed: use https://, http://, mailto: or a link "
                             "placeholder.")  # fmt: skip
    if required and not used & set(required):
        either = " or ".join(f"{{{{ {name} }}}}" for name in required)
        found.append(f"This e-mail must contain {either}.")
    return list(dict.fromkeys(found))


# ---------------------------------------------------------------- turning it into an e-mail


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, Block) and not value)


def _plain(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Block):
        return value.text
    return str(value)


def _safe_url(value: Any) -> str:
    """A value used as an address: only web and mail addresses, never javascript: or data:."""
    text = _plain(value).strip()
    return text if _LITERAL_TARGET.match(text) else ""


def fill(text: str, values: Mapping[str, Any]) -> str:
    """A one-line text (subject, title) with its placeholders replaced: plain text, escaped later by the layout."""
    filled = PLACEHOLDER.sub(lambda m: _plain(values.get(m.group(1))), text)
    return " ".join(filled.split())


def _drop(line: str, values: Mapping[str, Any]) -> bool:
    names = PLACEHOLDER.findall(line)
    return bool(names) and all(_is_empty(values.get(name)) for name in names)


def _shape(line: str, values: Mapping[str, Any], accent: str = "#714B67") -> tuple[str, str]:
    """One line of text as (HTML, text): placeholders, bold and links. What was typed and every value are escaped."""
    line = _SENTINEL.sub("", line)
    found: list[str] = []

    def mark(match: re.Match[str]) -> str:
        found.append(match.group(1))
        return chr(0xE000 + len(found) - 1)

    marked = PLACEHOLDER.sub(mark, line)

    def value_of(char: str) -> Any:
        value = values.get(found[ord(char) - 0xE000])
        return _SENTINEL.sub("", value) if isinstance(value, str) else value

    def html_values(text: str) -> str:
        return _SENTINEL.sub(lambda m: html.escape(_plain(value_of(m.group(0)))), text)

    def text_values(text: str) -> str:
        return _SENTINEL.sub(lambda m: _plain(value_of(m.group(0))), text)

    def target_of(raw: str) -> str:
        if len(raw) == 1 and _SENTINEL.fullmatch(raw):
            return _safe_url(value_of(raw))
        return raw if _LITERAL_TARGET.match(raw) else ""

    html_parts: list[str] = []
    text_parts: list[str] = []
    position = 0
    for link in _LINK.finditer(marked):
        before = marked[position : link.start()]
        html_parts.append(_bold(html.escape(before)))
        text_parts.append(before)
        label, url = link.group(1), target_of(link.group(2))
        label_html = html_values(_bold(html.escape(label)))
        label_text = _BOLD.sub(r"\1", label)
        if url:
            html_parts.append(f'<a href="{html.escape(url, quote=True)}" style="color:{accent};">{label_html}</a>')
            text_parts.append(f"{label_text} ({url})" if text_values(label_text) != url else url)
        else:
            html_parts.append(label_html)
            text_parts.append(label_text)
        position = link.end()
    rest = marked[position:]
    html_parts.append(_bold(html.escape(rest)))
    text_parts.append(rest)
    shaped_html = html_values("".join(html_parts))
    shaped_text = text_values(_BOLD.sub(r"\1", "".join(text_parts)))
    return shaped_html, shaped_text


def _bold(escaped: str) -> str:
    return _BOLD.sub(r"<strong>\1</strong>", escaped)


_P = '<p style="margin:0 0 16px 0;">{}</p>'
_UL = '<ul style="margin:0 0 16px 0; padding-left:20px;">{}</ul>'
_LI = '<li style="margin:0 0 4px 0;">{}</li>'
_NOTE = (
    '<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="margin:0 0 16px 0; '
    'font-size:13px; background-color:#FFF8E6; border-left:3px solid #E6A700; border-radius:4px;">'
    '<tr><td style="padding:10px 14px; color:#5C4400;">{}</td></tr></table>'
)
_BUTTON = (
    '<p style="margin:0 0 16px 0; text-align:center;"><a href="{href}" style="display:inline-block; '
    "background-color:{accent}; border:1px solid {accent}; border-radius:5px; padding:8px 18px; font-size:13px; "
    'color:#FFFFFF; text-decoration:none;">{label}</a></p>'
)


def render(body: str, values: Mapping[str, Any], accent: str = "#714B67") -> tuple[Markup, str]:
    """The body as (HTML for the layout's content block, plain text). `values` are the placeholders' values."""
    colour = accent if re.fullmatch(r"#[0-9A-Fa-f]{6}", accent) else "#714B67"
    html_out: list[str] = []
    text_out: list[str] = []
    kind = ""  # "p", "ul" or "note": the block being collected
    lines_html: list[str] = []
    lines_text: list[str] = []

    def close() -> None:
        nonlocal kind
        if kind == "p":
            html_out.append(_P.format("<br>".join(lines_html)))
            text_out.append("\n".join(lines_text))
        elif kind == "ul":
            html_out.append(_UL.format("".join(_LI.format(item) for item in lines_html)))
            text_out.append("\n".join(f"- {item}" for item in lines_text))
        elif kind == "note":
            html_out.append(_NOTE.format("<br>".join(lines_html)))
            text_out.append("\n".join(lines_text))
        kind = ""
        lines_html.clear()
        lines_text.clear()

    def collect(wanted: str, line: str) -> None:
        nonlocal kind
        if kind != wanted:
            close()
            kind = wanted
        shaped_html, shaped_text = _shape(line, values, colour)
        lines_html.append(shaped_html)
        lines_text.append(shaped_text)

    for raw in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = raw.strip()
        if not stripped:
            close()
            continue
        if _drop(stripped, values):
            continue
        whole = PLACEHOLDER.fullmatch(stripped)
        if whole and isinstance(values.get(whole.group(1)), Block):
            close()
            block = values[whole.group(1)]
            html_out.append(str(block.html))
            text_out.append(block.text)
            continue
        button = _LINK.fullmatch(stripped)
        if button:
            close()
            shaped_html, shaped_text = _shape(stripped, values, colour)
            href = re.search(r'href="([^"]*)"', shaped_html)
            if href:
                label_html = re.sub(r"^<a [^>]*>|</a>$", "", shaped_html)
                html_out.append(_BUTTON.format(href=href.group(1), label=label_html, accent=colour))
                label_text = _shape(button.group(1), values)[1]
                text_out.append(f"{label_text}: {html.unescape(href.group(1))}")
            else:
                html_out.append(_P.format(shaped_html))
                text_out.append(shaped_text)
            continue
        if stripped.startswith("- ") or stripped == "-":
            collect("ul", stripped[2:])
        elif stripped.startswith(">"):
            collect("note", stripped[1:].strip())
        else:
            collect("p", stripped)
    close()
    return Markup("".join(html_out)), "\n\n".join(part for part in text_out if part.strip())
