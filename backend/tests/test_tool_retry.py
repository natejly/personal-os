"""Toolbox.call retries a read-only tool after a transient network failure, and nothing else."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="rttest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os.tools import ToolSpec, _obj  # noqa: E402

CALLS: dict[str, int] = {}


def _spec(name: str, danger: str, fails: int, exc: type[BaseException] = httpx.ConnectError, group: str = "web") -> None:
    async def fn(ctx: dict[str, Any]) -> Any:
        CALLS[name] = CALLS.get(name, 0) + 1
        if CALLS[name] <= fails:
            raise exc("down")
        return {"ok": True}
    appmod.toolbox.specs[name] = ToolSpec(name, name, _obj({}, []), fn, group, danger)


_spec("rt_flaky", "network", 2)
_spec("rt_write", "writes", 2)
_spec("rt_ext", "external", 2)
_spec("rt_value", "safe", 1, ValueError)
_spec("rt_always", "safe", 99, asyncio.TimeoutError)
_spec("rt_browse", "network", 2, group="browser")


def go(name: str, retries: int = 2) -> Any:
    CALLS.clear()
    appmod.db.set_settings({"toolReadRetries": retries})
    slept: list[float] = []
    real = asyncio.sleep

    async def nosleep(d: float, *a: Any) -> None:
        slept.append(d)
    asyncio.sleep = nosleep  # type: ignore[assignment]
    try:
        return asyncio.run(appmod.toolbox.call(name, {}, {"tainted": False})), slept
    finally:
        asyncio.sleep = real  # type: ignore[assignment]


def test_flaky_read_succeeds_on_third_try() -> None:
    out, slept = go("rt_flaky")
    assert out == {"ok": True} and CALLS["rt_flaky"] == 3 and len(slept) == 2


def test_writes_and_state_are_never_retried() -> None:
    for n in ("rt_write", "rt_ext", "rt_browse"):
        out, _ = go(n)
        assert out.get("error") and CALLS[n] == 1, n


def test_non_transient_error_not_retried() -> None:
    out, _ = go("rt_value")
    assert out.get("error") and CALLS["rt_value"] == 1


def test_zero_disables_and_cap_holds() -> None:
    out, _ = go("rt_flaky", retries=0)
    assert out.get("error") and CALLS["rt_flaky"] == 1
    out, _ = go("rt_always", retries=2)
    assert out.get("error") and CALLS["rt_always"] == 3


def test_cancel_is_respected() -> None:
    async def run() -> None:
        CALLS.clear()
        appmod.db.set_settings({"toolReadRetries": 2})
        t = asyncio.create_task(appmod.toolbox.call("rt_always", {}, {"tainted": False}))
        await asyncio.sleep(0.05)   # inside the real backoff sleep
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            return
        raise AssertionError("not cancelled")
    asyncio.run(run())
