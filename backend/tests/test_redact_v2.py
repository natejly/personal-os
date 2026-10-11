"""Redaction v2: validators, context scoring, URL sanitizing.

Pure stdlib: no network, no model. Runs under pytest, or directly:
python backend/tests/test_redact_v2.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import redact  # noqa: E402


def v2(text: str) -> str:
    out = text
    for s in reversed(redact.analyze(text)):
        out = out[:s.start] + s.replacement + out[s.end:]
    return out


def test_luhn_card_redacted_but_plain_ids_are_not() -> None:
    assert "[card-number]" in v2("pay with 4111 1111 1111 1111 thanks")
    assert v2("ts 1727800000000 done") == "ts 1727800000000 done"
    assert v2("order 1234567812345678 shipped") == "order 1234567812345678 shipped"
    # ...but the same non-Luhn run is caught when the text says it is a card.
    assert "[card-number]" in v2("card number: 1234567812345678")


def test_ssn_validator_and_context() -> None:
    assert v2("id 666-12-3456 ok") == "id 666-12-3456 ok"
    assert "[ssn]" in v2("id 123-45-6789 ok")
    assert "[ssn]" in v2("my ssn is 666-12-3456")


def test_phone_validator() -> None:
    assert "[phone]" in v2("call 415-555-0134 now")
    assert "[phone]" not in v2("x 0000000000 y")
    assert "[phone]" not in v2("epoch 1727800000 y")
    assert "[phone]" in v2("+1 415 555 0134")


def test_entropy_validator_and_hash_context() -> None:
    tok = "aB3xK9mQ2zR7vL5nP8wY1tU4cD6fH0jS"
    assert "[redacted]" in v2(f"value {tok} end")
    assert v2("a1" * 16) == "a1" * 16                       # long but boring
    sha = "9fceb02d0ae598e95dc970b74767f19372d61af8"
    assert v2(f"commit {sha}") == f"commit {sha}"


def test_threshold_drops_borderline() -> None:
    text = "card number: 1234567812345678"          # fails Luhn, kept on context at 0.5
    assert redact.analyze(text, threshold=0.4)
    assert not redact.analyze(text, threshold=0.6)


def test_sanitize_url() -> None:
    assert redact.sanitize_url("https://x.com/cb?code=ABC123&state=xyz#access_token=zzz") == "https://x.com/cb?code=~&state=~"
    assert redact.sanitize_url("https://user:pw@example.com/a?token=abc") == "https://example.com/a?token=~"
    long = "x" * 80
    assert redact.sanitize_url(f"https://e.com/s?q={long}&page=2") == f"https://e.com/s?q={'x' * 32}&page=2"
    assert redact.sanitize_url("https://e.com/reset/aB3xK9mQ2zR7vL5nP8wY1tU4cD6fH0jS/now") == "https://e.com/reset/:redacted/now"
    assert redact.sanitize_url("https://e.com/docs/getting-started") == "https://e.com/docs/getting-started"
    for bad in ("", "not a url", None, 12):
        assert redact.sanitize_url(bad) == ""  # type: ignore[arg-type]


def test_v1_api_unchanged() -> None:
    assert list(redact.REDACTIONS) == [redact.RULES[k] for k in redact.ALL_RULES]
    assert redact.scrub("4111 1111 1111 1111") == "[card-number]"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
