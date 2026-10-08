"""Provider auth breaker: repeated key rejections fail fast, pause scheduled jobs, and clear on success or key change.

Run: backend/.venv/bin/python -m pytest backend/tests/test_auth_breaker.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="authbreaker-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402

from personal_os import auth_breaker as ab, llm  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.jobs import Jobs  # noqa: E402
from personal_os.jobs_policy import JobPolicy  # noqa: E402
from personal_os.runs import RunStore  # noqa: E402

S = {"baseUrl": "http://proxy.test/v1", "apiKey": "sk-dead", "llmRetries": 0}
BAD = '{"error": {"message": "Invalid API key"}}'


@pytest.fixture(autouse=True)
def fresh(monkeypatch: Any) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(ab, "clock", lambda: now[0])
    ab._state.clear()
    yield now
    ab._state.clear()


def test_opens_after_consecutive_rejections_and_announces_once() -> None:
    seen: list[dict[str, Any]] = []
    ab.on_change(seen.append)
    try:
        ab.failure(S, 401, BAD)
        assert ab.check(S) is None
        ab.failure(S, 401, BAD)
        assert "Settings" in (ab.check(S) or "")
        ab.failure(S, 401, BAD)
        assert [e["open"] for e in seen] == [True]
    finally:
        ab._listeners.remove(seen.append)


def test_non_key_failures_never_count() -> None:
    for status, body in [(403, '{"error": {"message": "key not allowed to access model gpt-x"}}'),
                         (429, BAD), (500, BAD), (400, '{"error": {"message": "bad request"}}')]:
        ab.failure(S, status, body)
        ab.failure(S, status, body)
    assert ab.check(S) is None


def test_success_resets_and_new_key_is_not_held() -> None:
    ab.failure(S, 401, BAD)
    ab.success(S)
    ab.failure(S, 401, BAD)
    assert ab.check(S) is None  # the success broke the streak
    ab.failure(S, 401, BAD)
    assert ab.blocked(S)
    assert ab.blocked({**S, "apiKey": "sk-new"}) is None  # a changed key has a different fingerprint
    ab.success(S)
    assert ab.blocked(S) is None


def test_cooldown_lets_one_probe_through(fresh: list[float]) -> None:
    ab.failure(S, 401, BAD)
    ab.failure(S, 401, BAD)
    fresh[0] += ab.COOLDOWN_S + 1
    assert ab.check(S) is None  # the probe
    assert ab.check(S)  # everyone behind it still fails fast
    ab.failure(S, 401, BAD)  # probe rejected: another cooldown
    fresh[0] += ab.COOLDOWN_S - 1
    assert ab.blocked(S)


def test_key_is_never_stored() -> None:
    ab.failure(S, 401, BAD)
    ab.failure(S, 401, BAD)
    assert "sk-dead" not in repr(ab._state)


def _drain(settings: dict[str, Any], calls: list[int]) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, text=BAD)

    async def go() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            async for _ in llm._send_attempts(c, settings, {"model": "m", "messages": []}, stream=False):
                pass

    asyncio.run(go())


def test_llm_fails_fast_once_open() -> None:
    calls: list[int] = []
    for _ in range(2):
        with pytest.raises(llm.LLMError):
            _drain(S, calls)
    assert len(calls) == 2
    with pytest.raises(llm.LLMError) as e:
        _drain(S, calls)
    assert len(calls) == 2 and e.value.kind == "auth"  # no HTTP request was made


def test_scheduled_job_is_skipped_while_open() -> None:
    db = Database(tempfile.mkdtemp(prefix="ab-"))
    jobs = Jobs(db)

    async def launch(_job: dict[str, Any], _fire: dict[str, Any]) -> str:
        return "r"

    policy = JobPolicy(jobs, RunStore(db), launch, clock=lambda: 1.0, settings=lambda: S)
    job = jobs.create("j", "0 * * * *", "p", timezone="UTC", enabled=True, at=0.0)
    assert asyncio.run(policy.admit(job, {"due_at": 1.0})) == (True, None)
    ab.failure(S, 401, BAD)
    ab.failure(S, 401, BAD)
    ok, why = asyncio.run(policy.admit(job, {"due_at": 1.0}))
    assert not ok and "Settings" in (why or "")
