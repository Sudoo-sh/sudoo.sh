"""The Arabic of job and step titles on the pages the gateway writes (instances/job_titles_ar.py) is the same as the
panel's (web/src/i18n/jobs.ts): both are checked against tests/job_titles_ar.json, the panel's Arabic for a sample
of every title the server writes (web/src/lib/job-titles.test.ts reads the same file). After changing the words,
write the file again: `cd web && UPDATE_JOB_TITLES=1 npx vitest run src/lib/job-titles.test.ts`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sudoo.instances.job_titles_ar import translate

EXPECTED: dict[str, str] = json.loads((Path(__file__).parent / "job_titles_ar.json").read_text())


@pytest.mark.parametrize("english", sorted(EXPECTED))
def test_same_arabic_as_the_panel(english: str) -> None:
    assert translate(english) == EXPECTED[english]
