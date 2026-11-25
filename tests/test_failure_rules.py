"""Rules that name the cause of a failed step (core/failure_rules.py) and how they join "Why it failed"."""

from __future__ import annotations

import pytest

from sudoo.core import failure, failure_rules

CASES = [
    (
        "odoo_unmet_dependency",
        "2026-01-01 10:00:00,000 7 WARNING db odoo.modules.graph: module sale_extra: Unmet dependencies: sale_base, crm_x",
        {"module": "sale_extra", "package": "sale_base, crm_x"},
    ),
    (
        "odoo_xml_parse_error",
        "odoo.tools.convert.ParseError: while parsing /mnt/extra-addons/shop/views/menu.xml:42, somewhere",
        {"file": "/mnt/extra-addons/shop/views/menu.xml", "line": "42"},
    ),
    (
        "odoo_external_id_missing",
        "ValueError: External ID not found in the system: shop.view_order_form",
        {"module": "shop", "file": "view_order_form"},
    ),
    (
        "python_syntax_error",
        'File "shop/models/order.py", line 17\n    def broken(:\n               ^\nSyntaxError: invalid syntax',
        {"file": "shop/models/order.py", "line": "17"},
    ),
    (
        "pip_no_matching_distribution",
        "ERROR: No matching distribution found for fancy-lib==9.9",
        {"package": "fancy-lib"},
    ),
    (
        "pip_no_matching_distribution",
        "ERROR: Could not find a version that satisfies the requirement other-lib>=3",
        {"package": "other-lib"},
    ),
    ("pip_conflict", "ERROR: ResolutionImpossible: for help visit the pip docs", {}),
    ("python_module_missing", "ModuleNotFoundError: No module named 'barcode_x'", {"package": "barcode_x"}),
    (
        "port_in_use",
        "Bind for 0.0.0.0:8069 failed: port is already allocated",
        {},
    ),
    ("access_csv_error", "ParseError: file shop/security/ir.model.access.csv could not be processed", {}),
]


@pytest.mark.parametrize(("rule_id", "line", "captured"), CASES)
def test_each_rule_matches_its_sample_and_captures(rule_id: str, line: str, captured: dict[str, str]) -> None:
    found = failure_rules.match(line.splitlines())
    assert found is not None
    assert found["id"] == rule_id
    assert found["captured"] == captured or set(captured) <= set(found["captured"])  # type: ignore[arg-type]
    for key, value in captured.items():
        assert found["captured"][key] == value  # type: ignore[index]
    assert found["title"] and found["explanation"] and found["fix"]


@pytest.mark.parametrize(
    "line",
    [
        "Everything worked",
        'File "shop/models/order.py", line 17, in compute\n    value = 1 / 0\nZeroDivisionError: division by zero',
        "WARNING odoo.modules.graph: module shop: loaded",
    ],
)
def test_ordinary_lines_and_other_tracebacks_match_no_rule(line: str) -> None:
    assert failure_rules.match(line.splitlines()) is None


def test_every_rule_id_is_unique_and_has_text() -> None:
    ids = [rule.id for rule in failure_rules.RULES]
    assert len(ids) == len(set(ids))
    assert all(rule.title and rule.explanation and rule.fix for rule in failure_rules.RULES)


def test_describe_adds_the_rule_and_keeps_the_rest() -> None:
    log = "\n== Install requirements\n$ pip install -r requirements.txt\nERROR: No matching distribution found for fancy-lib\n"
    described = failure.describe("Install requirements", "Step failed", log)
    assert described["kind"] == "requirements"
    assert described["rule"]["id"] == "pip_no_matching_distribution"
    assert described["rule"]["captured"]["package"] == "fancy-lib"
    assert any("fancy-lib" in line for line in described["details"])


def test_describe_without_a_known_cause_has_no_rule_key() -> None:
    described = failure.describe("Start Odoo", "Step failed", "\n== Start Odoo\nsomething odd happened\n")
    assert "rule" not in described
    assert described["kind"] == "other"


def test_captured_values_are_cleaned_of_server_paths_and_tokens() -> None:
    log = (
        "\n== Install\nodoo.tools.convert.ParseError: while parsing "
        "/opt/sudoo/instances/abc/addons/shop/views/a.xml:7, ghp_" + "A" * 30 + "\n"
    )
    found = failure.describe("Install", "failed", log)["rule"]
    assert found["captured"]["file"] == "shop/views/a.xml"
    assert "ghp_" not in str(found)


def test_the_panel_english_text_is_the_servers_text() -> None:
    """web/src/i18n/failure.ts holds the same English as the rules (the Arabic is keyed by the same ids)."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "web/src/i18n/failure.ts").read_text(encoding="utf-8")
    english = source.split("const ar: typeof en")[0]
    arabic = source.split("const ar: typeof en")[1]
    for rule in failure_rules.RULES:
        assert f"{rule.id}: {{" in english and f"{rule.id}: {{" in arabic
        for text in (rule.title, rule.explanation, rule.fix):
            assert text in english.replace('\\"', '"'), (rule.id, text)


def test_the_card_can_point_at_the_matched_line_of_the_shown_excerpt() -> None:
    log = (
        "\n== Install requirements\n$ pip install -r requirements.txt\nCollecting a\n"
        "ERROR: No matching distribution found for fancy-lib\nERROR: other\n"
    )
    described = failure.describe("Install requirements", "Step failed", log)
    line = described["rule"]["excerpt_line"]
    assert "No matching distribution" in described["details"][line - 1]
    assert "matched" not in described["rule"]  # internal only


def test_no_excerpt_line_when_the_matched_text_is_not_among_the_shown_lines() -> None:
    log = "\n== Start\nWARNING odoo.modules.graph: module a: Unmet dependencies: b\nERROR odoo: failed x\n"
    described = failure.describe("Start", "Step failed", log)
    assert described["rule"]["id"] == "odoo_unmet_dependency"
    assert "excerpt_line" not in described["rule"]


def test_a_rule_replaces_the_older_pattern_hint_so_one_failure_names_one_cause() -> None:
    from sudoo.core import hints

    for log in (
        "Traceback (most recent call last):\nModuleNotFoundError: No module named 'barcode'\n",
        "odoo.tools.convert.ParseError: while parsing /mnt/extra-addons/shop/views/menu.xml:42, somewhere\nXMLSyntaxError: bad\n",
    ):
        old = hints.friendly_hint(log)
        d = failure.describe("Update", old, log)
        assert "rule" in d
        assert d["reason"] == f"{d['rule']['title']}. {d['rule']['explanation']}"
        assert d["reason"] != old
        assert hints.GENERAL_HINT not in d["reason"]


def test_a_reason_that_is_not_a_pattern_hint_is_kept_next_to_a_rule() -> None:
    log = "ModuleNotFoundError: No module named 'barcode'\n"
    d = failure.describe("Update", "The update failed and was put back.", log)
    assert d["reason"] == "The update failed and was put back."
    assert d["rule"]["id"] == "python_module_missing"


def test_without_a_rule_the_older_hint_stays_exactly() -> None:
    from sudoo.core import hints

    log = "something unrecognisable happened\n"
    old = hints.friendly_hint(log)
    d = failure.describe("Update", old, log)
    assert "rule" not in d and d["reason"] == old


def test_a_step_s_own_explanation_wins_over_a_rule() -> None:
    from sudoo.core import hints

    log = "ModuleNotFoundError: No module named 'barcode'" + hints.panel_hint("The disk is full.")
    d = failure.describe("Update", hints.friendly_hint(log), log)
    assert "rule" not in d and d["reason"] == "The disk is full."
