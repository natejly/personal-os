"""Stuck detector: loop shapes the identical-call breaker cannot see.

app.REPEAT_LIMIT only fires on five identical calls in a row. A model can also ping-pong between two
calls, keep re-reading something that never changes, or fail the same way with slightly varied
arguments. This watches (tool, args, result) per executed call and names the pattern. Pure: no app
imports, no I/O. A repeat whose result changes (paging with an offset) is never flagged.
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from typing import Any

from .runs import args_digest

SAME_RESULT = 4     # one call, one result, this many times running
ERROR_CYCLE = 3     # one call erroring this many times running
ALTERNATIONS = 6    # A,B,A,B,A,B with nothing new coming back
ERROR_STORM = 4     # one tool erroring this many times running, with different args
WINDOW = 24

STUCK_NUDGE = ("You appear to be stuck: {detail}. Change approach: use a different tool or arguments, "
               "or answer with what you have and say what is missing.")
STUCK_STOP = ("This reply keeps getting stuck ({detail}), so it is stopping tool use. "
              "Answer with what you already have, and say in one line what you could not finish.")

# Keys whose value differs between otherwise identical runs of a call.
VOLATILE = {"duration_ms", "ts", "timestamp", "timestamps", "idempotency_key", "replayed", "elapsed_ms", "elapsed"}


def _strip(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _strip(x) for k, x in v.items() if k not in VOLATILE}
    if isinstance(v, list):
        return [_strip(x) for x in v]
    return v


def result_digest(result: Any) -> str:
    canon = json.dumps(_strip(result), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


@dataclass(frozen=True)
class Obs:
    tool: str
    args_digest: str
    result_digest: str
    error: bool

    @property
    def call(self) -> tuple[str, str]:
        return (self.tool, self.args_digest)


@dataclass(frozen=True)
class Stuck:
    pattern: str
    tool: str
    detail: str


@dataclass(frozen=True)
class Limits:
    same_result: int = SAME_RESULT
    error_cycle: int = ERROR_CYCLE
    alternations: int = ALTERNATIONS
    error_storm: int = ERROR_STORM


class StuckDetector:
    def __init__(self, limits: Limits | None = None) -> None:
        self.limits = limits or Limits()
        self.obs: deque[Obs] = deque(maxlen=WINDOW)

    def observe(self, tool: str, args: Any, result: Any) -> None:
        err = bool(result.get("error")) if isinstance(result, dict) else False
        self.obs.append(Obs(tool, args_digest(args), result_digest(result), err))

    def repeat_count(self, tool: str, args: Any) -> int:
        """How many of the most recent executed calls were exactly this call (same tool, same arguments).

        permrules reads it to put a card in front of the third identical call in a row."""
        key, n = (tool, args_digest(args)), 0
        for o in reversed(self.obs):
            if o.call != key:
                break
            n += 1
        return n

    def _tail(self, n: int) -> list[Obs]:
        return list(self.obs)[-n:] if len(self.obs) >= n else []

    def check(self) -> Stuck | None:
        lim = self.limits
        t = self._tail(lim.same_result)
        if t and len({(o.tool, o.args_digest, o.result_digest) for o in t}) == 1:
            return Stuck("same_result", t[0].tool, f"{t[0].tool} returned the same result {len(t)} times in a row")
        t = self._tail(lim.error_cycle)
        if t and all(o.error for o in t) and len({o.call for o in t}) == 1:
            return Stuck("error_cycle", t[0].tool, f"{t[0].tool} failed {len(t)} times with the same arguments")
        t = self._tail(lim.alternations)
        if t:
            a, b = t[0], t[1]
            if a.call != b.call and all(o == (a if i % 2 == 0 else b) for i, o in enumerate(t)):
                return Stuck("alternating", a.tool, f"{a.tool} and {b.tool} keep being called in turn and nothing new comes back")
        t = self._tail(lim.error_storm)
        if t and all(o.error for o in t) and len({o.tool for o in t}) == 1 and len({o.args_digest for o in t}) > 1:
            return Stuck("error_storm", t[0].tool, f"{t[0].tool} failed {len(t)} times in a row with different arguments")
        return None
