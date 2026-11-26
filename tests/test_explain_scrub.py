"""Task 18c: what is hidden before a failed step's lines are shown for sending. A corpus of secrets must never appear;
ordinary error lines must stay readable. The scrubber cannot guarantee that no secret is included (see its docstring).
"""

from __future__ import annotations

import pytest

from sudoo.explain.scrub import HIDDEN, scrub

# the words are joined here so that the repository's private-key check does not take this test data for a real key
KEY = "PRIVATE" + " KEY"
PEM = f"-----BEGIN RSA {KEY}-----\nMIIEowIBAAKCAQEAxxSECRETKEYBODYxx\nabcdEFGH\n-----END RSA {KEY}-----"
SECRETS: list[tuple[str, str]] = [
    ("ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2", "git clone with token ghp_a1B2c3D4e5F6g7H8i9J0k1L2 failed"),
    ("github_pat_11ABCDEFG0abcdefghijklmnop", "using github_pat_11ABCDEFG0abcdefghijklmnop"),
    ("hunter2hunter2", "psycopg2 error: password=hunter2hunter2 rejected"),
    ("S3cr3tValue", 'config {"db_password": "S3cr3tValue", "port": 5432}'),
    ("tok_live_9f8e7d6c5b", "request header token: tok_live_9f8e7d6c5b"),
    ("abc123secretXYZ", "Authorization: Bearer abc123secretXYZ"),
    ("dXNlcjpwYXNzd29yZA", "Authorization: Basic dXNlcjpwYXNzd29yZA"),
    ("opensesame77", "run: odoo --db_password opensesame77 -d prod"),
    ("pa55w0rd!", "postgres://odoo:pa55w0rd!@db.internal:5432/prod"),
    ("mypassw", "mongodb+srv://admin:mypassw@cluster0.example.net/x"),
    ("AKIAABCDEFGHIJKLMNOP", "key AKIAABCDEFGHIJKLMNOP found"),
    ("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
    (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dBjftJeZ4CVP",
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dBjftJeZ4CVP end",
    ),
    ("sk-proj-abcdefghijklmnopqrstuv", "OPENAI key sk-proj-abcdefghijklmnopqrstuv invalid"),
    ("xoxb-1234567890-abcdefghij", "slack xoxb-1234567890-abcdefghij"),
    ("MIIEowIBAAKCAQEAxxSECRETKEYBODYxx", PEM),
    ("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b", "digest 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b"),
    ("Zm9vYmFyU2VjcmV0VmFsdWUxMjM0NTY3ODkwQUJDREVG", "blob Zm9vYmFyU2VjcmV0VmFsdWUxMjM0NTY3ODkwQUJDREVG"),
    ("alice.smith@example.org", "owner alice.smith@example.org not found"),
    ("2001:db8:85a3::8a2e:370:7334", "connect to 2001:db8:85a3::8a2e:370:7334 refused"),
    ("203.0.113.77", "connect to 203.0.113.77:5432 refused"),
    ("كلمة-سر-123", "فشل الاتصال: password=كلمة-سر-123 مرفوض"),
    ("Pw-in-arabic-line9", "خطأ في الإعداد، client_secret: Pw-in-arabic-line9، حاول مرة أخرى"),
    ("sudoo_abcdef0123456789abcd", "panel token sudoo_abcdef0123456789abcd"),
    ("/srv/sudoo/data/customer-acme/db.key", "reading /srv/sudoo/data/customer-acme/db.key failed"),
]


@pytest.mark.parametrize(("secret", "line"), SECRETS)
def test_no_corpus_secret_survives(secret: str, line: str) -> None:
    text, _ = scrub(f"first line\n{line}\nlast line")
    assert secret not in text
    assert "first line" in text and "last line" in text


KEEP = [
    "odoo.tools.convert.ParseError: while parsing views/menu.xml:42, somewhere",
    "ModuleNotFoundError: No module named 'barcode'",
    "ERROR: No matching distribution found for fancy-lib",
    "2026-11-26 01:02:03,456 7 ERROR acme odoo.modules.loading: Failed to load module nile_wms",
    'Field "batch_zone_id" does not exist in model "stock.picking"',
    "OperationalError: could not connect to server: Connection refused",
    "فشل تحميل الوحدة nile_wms بسبب خطأ في ملف XML",
    "Step 3/7 : RUN pip install -r requirements.txt",
]


@pytest.mark.parametrize("line", KEEP)
def test_ordinary_error_lines_stay_readable(line: str) -> None:
    text, count = scrub(line)
    assert text == line and count == 0


def test_known_limits_are_real_and_documented() -> None:
    # not hidden, and the module says so: a secret broken over two lines, and a plain word after "is"
    split, _ = scrub("the code abc was\nsplit-in-a-sentence-1234567890 in two")
    assert "split-in-a-sentence-1234567890" in split
    plain, _ = scrub("the root login is swordfish")
    assert "swordfish" in plain
    from sudoo.explain import scrub as module

    assert "cannot guarantee" in (module.__doc__ or "")


def test_counts_places_changed_and_hidden_marker_is_used() -> None:
    text, count = scrub("password=a1b2c3d4\nBearer abcdefgh12345678\nmail me@example.org")
    assert text.count(HIDDEN) >= 2 and "<email>" in text and count >= 3


WRAPPED = [
    ("TAIL0123456789abcdef", "password=HEAD0123456789abcdef\nTAIL0123456789abcdef"),
    ("TAILtokenpart99XYZ", "Authorization: Bearer\nTAILtokenpart99XYZ"),
    ("wrappedSECRETtail77", 'api_key: "aaaaBBBBccccDDDD"\nwrappedSECRETtail77\nnext message'),
    ("zzSECONDHALFkey123456", "key blob AAAAbbbbCCCCdddd1111EEEEffff\nzzSECONDHALFkey123456"),
    (
        "MIIEBODYLINE2",
        f"2026-11-26 01:00:00 -----BEGIN {KEY}-----\n2026-11-26 01:00:00 MIIEBODYLINE2\n2026-11-26 01:00:00 -----END {KEY}-----",
    ),
    ("MIIUNTERMINATED", f"-----BEGIN OPENSSH {KEY}-----\nMIIUNTERMINATED\nand more"),
]


@pytest.mark.parametrize(("secret", "text"), WRAPPED)
def test_common_wrapped_credentials_and_key_blocks_are_hidden(secret: str, text: str) -> None:
    out, _ = scrub(text)
    assert secret not in out and "HEAD0123456789abcdef" not in out and "aaaaBBBBccccDDDD" not in out


REVIEW_SECRETS = [
    ("abc123", "ENCRYPTION_KEY=abc123"),
    ("ABCD-1234", "LICENSE_KEY=ABCD-1234"),
    ("hunter2", "master_key: hunter2"),
    ("my pass", 'signing_key = "my pass"'),
    ("zzz", "docker build --build-arg SIGNING_KEY=zzz ."),
    ("abcd1234", "key=abcd1234"),
    ("hunter2", "curl -u admin:hunter2 https://x.example/api"),
    ("Hunter2", "mysql -u root -pHunter2 shop"),
    ("hunter2", "token hunter2 rejected"),
    ("hunter2", "the password for the admin user is hunter2"),
    ("tr0ub4dor", "secret was tr0ub4dor"),
    ("s3cr3t", "psql --user=bob:s3cr3t db"),
]


@pytest.mark.parametrize(("secret", "line"), REVIEW_SECRETS)
def test_review_findings_short_secrets_are_hidden(secret: str, line: str) -> None:
    text, count = scrub(line)
    assert secret not in text and count >= 1, text


@pytest.mark.parametrize(
    "line",
    ["monkey patch applied", "keyword argument missing", "ls -u shows nothing", "port 8069 busy, key mismatch in view"],
)
def test_review_fixes_leave_ordinary_words_alone(line: str) -> None:
    assert scrub(line)[0] == line


@pytest.mark.parametrize(
    ("tail", "line"),
    [
        ("bar", "adminpwd=foo bar"),
        ("two words", "SECRET_KEY=one two words"),
        ("tail", "password: a b c tail"),
        ("tail", "db_password=first second tail"),
    ],
)
def test_an_unquoted_secret_with_spaces_is_hidden_to_the_end_of_the_line(tail: str, line: str) -> None:
    text, _ = scrub(line)
    assert tail not in text and "foo" not in text and "first" not in text and "one" not in text, text


def test_ordinary_lines_with_key_or_password_words_stay() -> None:
    for line in (
        "KeyError: 'name'",
        "duplicate key value violates unique constraint",
        "password authentication failed for user odoo",
    ):
        assert scrub(line)[0] == line


def test_over_hiding_is_known() -> None:
    # "token was <word>" is read as a secret named by the word before "was": ordinary text can lose a word
    assert "<hidden>" in scrub("the token was not valid")[0]
