import pytest

from incident_agent.redaction import redact

# The fake secrets are built from parts, so secret scanners do not flag the source file.
SENTINELS = [
    "SENTINEL_4929384756",          # long digits
    "jane.doe@example.com",
    "dapi" + "0123456789abcdef" * 2,
    "AKIA" + "ABCDEFGHIJKLMNOP",
    f"eyJhbGciOiJIUzI1NiJ9.{'eyJzdWIiOiIxMjM0NTY3ODkwIn0'}.abcdefghijklmnop",
    "https://host/path?token=abcdef&user=x",
]


@pytest.mark.parametrize("secret", SENTINELS)
def test_sentinels_never_survive(secret):
    out = redact(f"Failure while processing value {secret} in stage 3")
    core = secret.split("?")[-1] if "?" in secret else secret
    assert core not in out


def test_digits_masked_but_short_numbers_kept():
    assert "<num>" in redact("id 1234567 failed")
    assert "line 42" in redact("error at line 42")


def test_key_value_secret_masked():
    out = redact("connect with password=hunter2 failed")
    assert "hunter2" not in out and "password=<secret>" in out


def test_string_literal_masked_but_identifiers_kept():
    out = redact("cannot cast 'John Smith lives here' ; column 'amount' missing")
    assert "John Smith" not in out
    assert "'amount'" in out


def test_truncates_keeping_tail():
    text = "word " * 2_000 + "ROOT CAUSE: column missing"
    out = redact(text, max_chars=200)
    assert out.endswith("ROOT CAUSE: column missing")
    assert len(out) < 300


def test_private_key_block_masked():
    out = redact("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----")
    assert "abc" not in out
