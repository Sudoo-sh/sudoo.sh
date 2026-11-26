"""A comparison view of one line of text, with the way back to the original (not used by scrub.py yet).

Hiding a secret by looking at a changed copy of the text is only safe when what is hidden is the original text at the
same place. `view(text)` gives the copy (compatibility-normalised per character, invisible format characters left
out) and, for each character of the copy, the position in the original it came from. `original_span` turns a range
of the copy into the range of the original to hide. Nothing from the copy is ever shown or sent.

Conservative on purpose: each character is normalised alone (NFKC), so a base letter followed by a combining mark is
not joined, and look-alike letters of another script are not folded. Those stay unseen by the copy and are limits.
"""

from __future__ import annotations

import unicodedata


def view(text: str) -> tuple[str, list[int]]:
    """(the copy of `text`, for each character of it the index of the original character it came from).

    A character of category Cf (zero-width space and joiners, direction marks) is left out of the copy; any other
    character becomes its NFKC form, one or several characters (the full-width "＝" becomes "="), each pointing at
    the same original character. Never raises and never changes the length relation: len(offsets) == len(copy)."""
    out: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(text):
        if unicodedata.category(char) == "Cf":
            continue
        for piece in unicodedata.normalize("NFKC", char):
            out.append(piece)
            offsets.append(index)
    return "".join(out), offsets


def original_span(offsets: list[int], start: int, end: int, length: int) -> tuple[int, int]:
    """The range (first, one past the last) of the original, `length` characters long, that covers the characters
    start..end of the copy, with every character left out between them. An empty or out-of-range request gives an
    empty range at a valid place, never an index outside the original."""
    start = max(0, min(start, len(offsets)))
    end = max(start, min(end, len(offsets)))
    if start == end:
        place = offsets[start] if start < len(offsets) else length
        return place, place
    return offsets[start], min(length, offsets[end - 1] + 1)
