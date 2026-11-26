"""The comparison view of a line and its way back to the original (explain/view.py). Synthetic text only."""

from __future__ import annotations

import random
import unicodedata

from sudoo.explain.view import original_span, view


def test_plain_text_is_unchanged() -> None:
    copy, offsets = view("password=hunter2")
    assert copy == "password=hunter2" and offsets == list(range(16))


def test_invisible_characters_are_left_out_and_the_span_covers_them() -> None:
    text = "pass\u200bword=hunter2"
    copy, offsets = view(text)
    assert copy == "password=hunter2"
    first, last = original_span(offsets, 0, len("password"), len(text))
    assert text[first:last] == "pass\u200bword"  # the hidden part of the original includes the invisible character


def test_a_full_width_equals_sign_is_seen_as_equals_and_hides_the_original_character() -> None:
    text = "password\uff1dhunter2"
    copy, offsets = view(text)
    assert copy == "password=hunter2"
    first, last = original_span(offsets, 8, 9, len(text))
    assert text[first:last] == "\uff1d"


def test_one_character_that_becomes_several_points_at_itself() -> None:
    text = "a\ufb01b"  # a ligature of f and i
    copy, offsets = view(text)
    assert copy == "afib" and offsets == [0, 1, 1, 2]
    assert original_span(offsets, 1, 2, len(text)) == (1, 2) and original_span(offsets, 2, 3, len(text)) == (1, 2)


def test_look_alike_letters_and_combining_marks_are_not_folded() -> None:
    assert view("p\u0430ssword")[0] == "p\u0430ssword"  # Cyrillic a stays Cyrillic: a documented limit
    assert view("e\u0301")[0] == "e\u0301"  # not joined: normalised one character at a time


def test_empty_and_out_of_range_requests_stay_inside_the_original() -> None:
    text = "ab"
    _, offsets = view(text)
    assert original_span(offsets, 0, 0, 2) == (0, 0)
    assert original_span(offsets, 2, 5, 2) == (2, 2)
    assert original_span(offsets, -3, 99, 2) == (0, 2)
    assert original_span([], 0, 3, 0) == (0, 0)


def test_properties_on_random_text() -> None:
    """For random text of ordinary, full-width, invisible, accented and symbol characters (fixed seed): the offsets
    match the copy, never decrease, stay inside the original, the copy is the per-character NFKC form of the
    original without its Cf characters, and every range of the copy maps to a range of the original that holds
    every original character the copy characters came from."""
    alphabet = list("abcPASWORDkey=:@/ \n\"'") + [
        "\u200b",
        "\u200d",
        "\u202e",
        "\uff1d",
        "\uff1a",
        "\ufb01",
        "\u0430",
        "\u00e9",
        "e\u0301",
        "\u00ad",
        "\u4e2d",
        "\U0001f600",
    ]
    rng = random.Random(20260126)
    for _ in range(400):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        copy, offsets = view(text)
        assert len(copy) == len(offsets)
        assert all(0 <= o < len(text) for o in offsets) and offsets == sorted(offsets)
        expected = "".join(unicodedata.normalize("NFKC", c) for c in text if unicodedata.category(c) != "Cf")
        assert copy == expected
        for _ in range(10):
            start = rng.randint(0, len(copy))
            end = rng.randint(start, len(copy))
            first, last = original_span(offsets, start, end, len(text))
            assert 0 <= first <= last <= len(text)
            if end > start:
                assert first == offsets[start] and last >= offsets[end - 1] + 1
                assert set(offsets[start:end]) <= set(range(first, last))
