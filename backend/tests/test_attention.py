import json
from pathlib import Path

from personal_os.attention import attention, for_desk, for_job, for_run

CASES = json.loads((Path(__file__).parent / "fixtures" / "attention_cases.json").read_text())


def test_fixture_table():
    for c in CASES:
        opts = {k: v for k, v in c.items() if k not in ("kind", "status", "expect")}
        assert attention(c["kind"], c["status"], **opts) == c["expect"], c


def test_adapters():
    assert for_run({"status": "awaiting_approval"}) == "needs_you"
    assert for_desk({"status": "failed"}) == "blocked"
    assert for_job({"last_error": None, "paused_reason": None}, "running") == "working"
    assert for_job({"last_error": None, "paused_reason": "expired"}, None) == "blocked"
    assert for_job({"last_error": None, "paused_reason": None}, "done", 1) == "needs_you"
