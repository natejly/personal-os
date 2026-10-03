"""Named redaction rules: the subsets, the order, and what meetings must NOT lose.

Pure regex, so there is nothing to stub: every test here is a string in and a string out.
The order tests matter most - the rule list is security-relevant and `REDACTIONS` is
positional, so a reordered dict has to fail loudly rather than quietly change what is stored.

Runs under pytest, or directly: python backend/tests/test_redact.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import redact  # noqa: E402

EMAIL = "ada@example.com"
PHONE = "+1 415 555 0134"
KEY = "sk-abcdefghijklmnopqrstuvwxyz012345"
PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAKj3\n-----END RSA PRIVATE KEY-----"


def test_scrub_secrets_keeps_the_people() -> None:
    """A transcript is a record of who said what to whom: identity survives, credentials do not."""
    out = redact.scrub_secrets(f"{EMAIL} {PHONE} {KEY}")
    assert EMAIL in out
    assert PHONE in out
    assert KEY not in out
    assert "[email]" not in out
    assert "[phone]" not in out
    # The token rule fires; SECRET_ASSIGN then swallows its own [token] marker - see below.
    assert "[secret]" in out


def test_token_rule_marker() -> None:
    """The token rule's replacement, before SECRET_ASSIGN gets to it."""
    out = redact.scrub(f"{EMAIL} {PHONE} {KEY}", ("token",), secret_assign=False)
    assert out == f"{EMAIL} {PHONE} [token]"


def test_secret_assign_eats_its_own_marker() -> None:
    """Inherited from activity.py: "token" is a SECRET_ASSIGN trigger word, so the [token]
    the token rule just inserted matches on the next pass. The value is still gone, the
    marker is just [secret] instead. Pinned so a future fix is a deliberate decision."""
    assert redact.scrub_secrets(KEY) == "[[secret]"


def test_default_rules_scrub_identity() -> None:
    out = redact.scrub(f"{EMAIL} {PHONE}")
    assert "[email]" in out
    assert "[phone]" in out
    assert EMAIL not in out
    assert "555" not in out


def test_private_key_block_collapses() -> None:
    assert redact.scrub(PRIVATE_KEY) == "[private-key]"
    assert redact.scrub_secrets(PRIVATE_KEY) == "[private-key]"


def test_secret_assign() -> None:
    assert "[secret]" in redact.scrub("the password is hunter2hunter2")
    assert "hunter2hunter2" not in redact.scrub("the password is hunter2hunter2")
    # Opt out and the sweep does not run at all.
    assert redact.scrub("the password is hunter2hunter2", secret_assign=False) == "the password is hunter2hunter2"


def test_other_rules() -> None:
    assert redact.scrub("ssn 123-45-6789") == "ssn [ssn]"
    assert "[aws-key]" in redact.scrub_secrets("AKIAIOSFODNN7EXAMPLE is the id")
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    assert pat not in redact.scrub_secrets(pat) and "[github-pat]" in redact.scrub_secrets(pat)
    gkey = "AIzaSyA1234567890abcdefGHIJKLMNOPQRSTUV"  # 39 chars, the real key length
    assert gkey not in redact.scrub_secrets(gkey) and "[google-key]" in redact.scrub_secrets(gkey)
    oauth = "ya29.a0AfH6SMCabcdefghijklmnopqrstuvwxyz"
    assert oauth not in redact.scrub_secrets(oauth) and "[google-access]" in redact.scrub_secrets(oauth)
    hook = "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
    assert "hooks.slack.com" not in redact.scrub_secrets(hook)
    assert "[card-number]" in redact.scrub_secrets("4111 1111 1111 1111")
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g"
    assert "[jwt]" in redact.scrub_secrets(jwt)
    assert "[redacted]" in redact.scrub_secrets("blob aG9sZEl0UmlnaHRUaGVyZTEyMzQ1Njc4OTA=")


def test_rule_order_is_the_contract() -> None:
    """activity.Gate.scrub applies REDACTIONS positionally, so the dict order IS behaviour."""
    assert redact.ALL_RULES == (
        "private_key", "url_userinfo", "url_secret_param", "email", "card", "ssn", "token", "aws_key",
        "github_pat", "google_api", "google_oauth", "slack_webhook", "jwt", "phone", "entropy",
    )
    assert redact.REDACTIONS == [redact.RULES[k] for k in redact.ALL_RULES]
    assert tuple(redact.RULES.values()) == tuple(redact.REDACTIONS)
    # The two rules meetings must never apply, named explicitly rather than inferred.
    assert "email" not in redact.SECRET_RULES
    assert "phone" not in redact.SECRET_RULES
    assert set(redact.SECRET_RULES) < set(redact.ALL_RULES)


def test_scrub_matches_the_redactions_pipeline() -> None:
    """Regression guard: scrub() with the defaults must stay byte-identical to what
    activity.Gate.scrub did - every pattern in REDACTIONS order, then SECRET_ASSIGN."""
    text = (
        f"mail {EMAIL} call {PHONE} key {KEY} aws AKIAIOSFODNN7EXAMPLE "
        f"ssn 123-45-6789 card 4111-1111-1111-1111 password hunter2hunter2 "
        f"blob aG9sZEl0UmlnaHRUaGVyZTEyMzQ1Njc4OTA= {PRIVATE_KEY}"
    )
    expected = text
    for pat, repl in redact.REDACTIONS:
        expected = pat.sub(repl, expected)
    expected = redact.SECRET_ASSIGN.sub("[secret]", expected)
    assert redact.scrub(text) == expected
    assert redact.scrub(text, redact.ALL_RULES) == expected


def test_empty_and_unknown_rules() -> None:
    assert redact.scrub("") == ""
    assert redact.scrub_secrets("") == ""
    # A stale rule name out of settings must not take a collector down.
    assert redact.scrub(f"{EMAIL} ok", ("nope", "email")) == "[email] ok"
    assert redact.scrub(f"{EMAIL} ok", ()) == f"{EMAIL} ok"


def test_url_userinfo_and_query_secrets_are_scrubbed() -> None:
    url = "https://ada:hunter2@bank.example/login?token=abc123&ok=1"
    out = redact.scrub_secrets(url)
    assert "hunter2" not in out
    assert "abc123" not in out
    assert "bank.example" in out
    frag = "https://app.example/cb#access_token=supersecretvalue"
    assert "supersecretvalue" not in redact.scrub(frag)
    assert "app.example" in redact.scrub(frag)
    upper = redact.scrub_secrets("HTTPS://ada:hunter2@bank.example/login?TOKEN=abc123")
    assert "hunter2" not in upper
    assert "abc123" not in upper
    assert "bank.example" in upper


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
