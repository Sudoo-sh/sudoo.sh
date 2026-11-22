"""Odoo's real test logs (tests/odoo_test_logs/real_*.log) read by projects/testing.parse_log.

Each log was captured from the official odoo:17.0, odoo:18.0 and odoo:19.0 images on PostgreSQL 16, running exactly
the command testing.command_args builds (`-d acme --test-enable --stop-after-init --no-http -u <module>
--test-tags=/<module>`) on a small module with a passing test, a failing assertion, a test raising an exception and a
post_install test (sbo_demo_tests), and on one whose tests all pass (sbo_demo_pass). Odoo's exit code was 1 for the
failing runs and 0 for the others. The container's host name is replaced by "odoo"; nothing else is changed."""

from __future__ import annotations

from pathlib import Path

import pytest

from sudoo.core.odoo_args import demo_args
from sudoo.projects import testing

LOGS = Path(__file__).resolve().parent / "odoo_test_logs"
FAILED = "At least one test failed when loading the modules (sbo_demo_tests). 1 failed, 1 with errors, of 4 tests."


@pytest.mark.parametrize("version", ["17", "18", "19"])
def test_a_real_failing_run(version: str) -> None:
    report = testing.parse_log((LOGS / f"real_odoo{version}_failed.log").read_text(), exit_code=1)
    assert report["status"] == "failed"
    assert report["result"] == FAILED  # 2 at_install tests failing of 3, and the post_install test passing
    assert (report["tests"], report["failed"], report["errors"]) == (4, 1, 1)
    assert report["modules"] == ["sbo_demo_tests"]
    # each failing test with its exception, the traceback's last line
    assert report["messages"][:2] == [
        "ERROR odoo.addons.sbo_demo_tests.tests.test_demo: ERROR: TestDemo.test_error | AttributeError: "
        "'res.partner' object has no attribute 'no_such_method'",
        "ERROR odoo.addons.sbo_demo_tests.tests.test_demo: FAIL: TestDemo.test_fail | AssertionError: 2 != 3 : "
        "arithmetic is broken on purpose",
    ]


@pytest.mark.parametrize("version", ["17", "18"])
def test_a_real_passing_run(version: str) -> None:
    report = testing.parse_log((LOGS / f"real_odoo{version}_passed.log").read_text())
    assert report["status"] == "success"
    assert report["result"] == "3 tests passed."  # two at_install tests and one post_install test
    assert report["messages"] == [] and report["modules"] == []


def test_odoo_19_warns_about_a_manifest_twice_and_it_counts_once() -> None:
    report = testing.parse_log((LOGS / "real_odoo19_passed_with_warnings.log").read_text())
    assert report["status"] == "warning"
    assert report["result"] == "Tests passed with 2 warnings in Odoo's log."  # 6 lines, 2 different warnings
    assert all("Missing `author` key in manifest" in line for line in report["messages"])


def test_a_real_run_of_custom_tags() -> None:
    log = (LOGS / "real_odoo18_custom_tags.log").read_text()
    assert "Starting TestDemo.test_pass" in log and "test_fail" not in log  # only the tagged test ran
    assert testing.parse_log(log)["result"] == "1 test passed."
    args = testing.command_args(
        "18.0", "custom", "/sbo_demo_tests:TestDemo.test_pass,-slow", ["sbo_demo_tests"], "acme"
    )
    assert args == ["-d", "acme", "--test-enable", "--stop-after-init", "--no-http", "-u", "sbo_demo_tests",
                    "--test-tags=/sbo_demo_tests:TestDemo.test_pass,-slow"]  # fmt: skip


def test_demo_data_arguments_by_version() -> None:
    # Odoo 18 and older load demo data unless told not to and refuse --with-demo ("no such option", exit code 2)
    assert demo_args("17.0", False) == ["--without-demo=all"] and demo_args("18.0", True) == []
    assert demo_args("saas-18.2", False) == ["--without-demo=all"]
    # Odoo 19 loads none unless asked
    assert demo_args("19.0", True) == ["--with-demo"] and demo_args("19.0", False) == []
    assert demo_args("20.0", False) == []


def test_every_module_leaves_themes_out() -> None:
    assert "theme_treehouse".startswith(testing.ALL_EXCLUDED)
    assert not "website_sale".startswith(testing.ALL_EXCLUDED)
