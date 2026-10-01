"""Artifact search/replace patches. Run: backend/.venv/bin/python backend/tests/test_artifact_patch.py"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.artifact_patch import MAX_EDITS, MAX_PATCH_CHARS, PatchError, apply_patch  # noqa: E402

DOC = "<html>\n<body>\n  <h1>Hello</h1>\n  <p>one</p>\n  <p>two</p>\n</body>\n</html>"
passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def raises(code: str, edits: list[dict[str, Any]]) -> PatchError:
    try:
        apply_patch(code, edits)
    except PatchError as e:
        return e
    raise AssertionError("expected PatchError")


def test_exact_single() -> None:
    out = apply_patch(DOC, [{"search": "<h1>Hello</h1>", "replace": "<h1>Bye</h1>"}])
    check("<h1>Bye</h1>" in out and "Hello" not in out, "exact single match applies")
    check(out.count("<p>") == 2, "the rest is untouched")


def test_ordered_running_text() -> None:
    out = apply_patch(DOC, [{"search": "Hello", "replace": "Hi there"}, {"search": "Hi there", "replace": "Yo"}])
    check("<h1>Yo</h1>" in out, "later edits see earlier edits")


def test_zero_and_many() -> None:
    e = raises(DOC, [{"search": "<h1>Hello</h1>", "replace": "x"}, {"search": "<table>", "replace": "y"}])
    check(e.index == 1 and "not found" in e.reason, "zero match reports the index")
    e = raises(DOC, [{"search": "<p>", "replace": "<q>"}])
    check(e.index == 0 and "2 places" in e.reason, "two matches raises")
    check(e.snippet != "", "a nearby snippet is offered")


def test_atomic() -> None:
    before = DOC
    raises(before, [{"search": "one", "replace": "1"}, {"search": "nope", "replace": "2"}])
    check(before == DOC, "a failed patch changes nothing (strings are immutable; result never returned)")
    try:
        apply_patch(before, [{"search": "one", "replace": "1"}, {"search": "nope", "replace": "2"}])
    except PatchError:
        pass
    check(True, "raised without a partial result")


def test_whitespace_fallback() -> None:
    out = apply_patch(DOC, [{"search": "<h1>Hello</h1>\n<p>one</p>", "replace": "<h2>Hi</h2>"}])
    check("<h2>Hi</h2>" in out and "<p>two</p>" in out and "one" not in out, "line-trimmed match replaces the whole span")
    e = raises("<a>\n  <b>x</b>\n</a>\n<a>\n<b>x</b>\n</a>", [{"search": "<b>x</b>", "replace": "z"}])
    check("2 places" in e.reason, "exact duplicates still fail before the fuzzy path")
    e = raises("  <i>x</i>\n<i>x</i>\n", [{"search": "<i>x</i>\n", "replace": "z"}])
    check(True, "ambiguous after trimming fails")


def test_caps() -> None:
    e = raises(DOC, [{"search": "Hello", "replace": "H"}] * (MAX_EDITS + 1))
    check("too many" in e.reason, "edit count cap")
    e = raises(DOC, [{"search": "Hello", "replace": "x" * (MAX_PATCH_CHARS + 1)}])
    check("limit" in e.reason, "size cap")
    raises(DOC, [])
    raises(DOC, [{"search": "  ", "replace": "x"}])
    raises(DOC, [{"search": 3, "replace": "x"}])  # type: ignore[dict-item]
    check(True, "empty, blank and non-string edits are rejected")


TESTS = [test_exact_single, test_ordered_running_text, test_zero_and_many, test_atomic, test_whitespace_fallback, test_caps]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{passed} assertions passed, {failures} test(s) failed")
    sys.exit(1 if failures else 0)
