"""Prove an external write happened by reading it back.

A write's own 200 is not evidence, and neither is the model's report of it: measured on real
tasks, agents claim completion they did not achieve about 45% of the time, and an independent
re-read of the remote state cuts that to about 3%. So every external write in google.py comes
back through check(), which fetches the object from the server again and compares the fields
that were written.

Three outcomes, never collapsed into "ok":
  verified    the re-read found it, and every compared field matches
  unverified  the re-read could not find it -- eventual consistency, or it never happened
  mismatch    the re-read found it but something we wrote is not what is stored (or, for a
              delete, it is still there)

Eventual consistency gets a short bounded backoff and nothing more: RETRY_DELAYS is a couple
of quick retries, not a loop, so an unprovable write is reported in seconds rather than
hanging the call. "Not yet visible" and "stored wrong" stay separate reasons because they ask
different things of the user.

Anything but `verified` is a failure the user has to see. tools.py turns it into a tool error
so the model cannot say "I sent that", and the HTTP layer hands it to the UI, which must not
render success.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Callable, Iterable, Sequence

log = logging.getLogger(__name__)

VERIFIED, UNVERIFIED, MISMATCH = "verified", "unverified", "mismatch"

# Two quick retries, ~2s of patience in total. Long enough for ordinary Google propagation,
# short enough that a write that never landed is reported while the user is still looking.
RETRY_DELAYS: tuple[float, ...] = (0.5, 1.5)
# Gmail indexes a sent message into SENT a beat later than Calendar exposes an event, so the
# mail ladder is one rung longer (~5s).
MAIL_RETRY_DELAYS: tuple[float, ...] = (0.5, 1.5, 3.0)

# Values longer than this are cut in the recorded diff: the journal row carries the outcome,
# not the payload.
VALUE_CAP = 120

# Indirected so a test can shorten the backoff without waiting it out. Read at call time, never
# bound as a default argument.
SLEEP: Callable[[float], None] = time.sleep


class NotVisible(Exception):
    """A read-back could not find the object (404/410, or an empty result)."""


def is_missing(e: BaseException) -> bool:
    """True when a Google client error means "no such object" rather than "call failed"."""
    status = getattr(getattr(e, "resp", None), "status", None)
    if status is not None:
        return status in (404, 410)  # a real status decides; a 5xx whose text mentions "410" is not "gone"
    s = str(e)
    return "404" in s or "410" in s or "not found" in s.lower()


def _cap(v: Any) -> Any:
    if isinstance(v, str) and len(v) > VALUE_CAP:
        return v[:VALUE_CAP] + "…"
    return v


def _instant(v: Any) -> Any:
    """A datetime/date string as a comparable instant; the raw value when it will not parse.

    Google echoes a written time back in its own offset form ("2026-10-07T15:00:00-04:00" for
    a local "2026-10-07T15:00"), so times have to be compared as instants, not as strings.
    """
    if not isinstance(v, str) or not v.strip():
        return v
    s = v.strip()
    if len(s) == 10:  # all-day: a plain date
        try:
            return dt.date.fromisoformat(s)
        except ValueError:
            return v
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return v
    return d.timestamp() if d.tzinfo else d.replace(microsecond=0).isoformat()


def _same(a: Any, b: Any, as_time: bool) -> bool:
    if as_time:
        return _instant(a) == _instant(b)
    if isinstance(a, str) and isinstance(b, str):
        return a.strip() == b.strip()
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        # Order-insensitive: a guest list and a set of RRULE lines mean the same thing in any
        # order, and Google does not promise to hand them back in the one it was given.
        return sorted(map(str, a)) == sorted(map(str, b))
    return a == b


def diff(want: dict[str, Any], got: dict[str, Any], time_fields: Iterable[str] = ()) -> dict[str, Any]:
    """The fields that disagree, as {field: {expected, actual}}. Empty means they match.

    A field whose expectation is None is not compared: we only ever wrote some of them.
    """
    times = set(time_fields)
    out: dict[str, Any] = {}
    for k, exp in want.items():
        if exp is None:
            continue
        act = got.get(k)
        if not _same(exp, act, k in times):
            out[k] = {"expected": _cap(exp), "actual": _cap(act)}
    return out


def check(what: str, read_back: Callable[[], Any], *, compare: Callable[[Any], dict[str, Any]] | None = None,
          compared: Sequence[str] = (), absent: bool = False, gone_if: Callable[[Any], bool] | None = None,
          delays: Sequence[float] | None = None, sleep: Callable[[float], None] | None = None) -> dict[str, Any]:
    """Re-read a write and judge it. Never raises: the verdict is the return value.

    read_back()  fetches the object from the server, raising NotVisible when it is not there.
    compare(obj) returns the fields that disagree (see diff); None means existence is enough.
    absent=True  inverts the test for a delete: being gone is the proof, and gone_if(obj) lets
                 a tombstone (a cancelled calendar event) count as gone.
    """
    delays = RETRY_DELAYS if delays is None else delays
    nap = sleep or SLEEP
    t0 = time.time()
    attempts, last_error, last_reason = 0, "", "not_visible"
    for i in range(len(delays) + 1):
        if i:
            nap(delays[i - 1])
        attempts += 1
        obj: Any = None
        present = True
        try:
            obj = read_back()
            last_reason = "not_visible"
        except NotVisible as e:
            present, last_error, last_reason = False, str(e), "not_visible"
        except Exception as e:  # noqa: BLE001 - a failed read is a failed proof, not a crash
            present, last_error = False, f"{type(e).__name__}: {(str(e).strip().splitlines() or [type(e).__name__])[0][:200]}"
            last_reason = "not_visible" if is_missing(e) else "read_failed"
            if last_reason == "read_failed":
                continue  # transient: retry, and report read_failed if it persists
        base = {"what": what, "compared": list(compared), "attempts": attempts}
        if absent:
            if not present:
                return {**base, "status": VERIFIED, "ms": _ms(t0)}
            if gone_if and gone_if(obj):
                return {**base, "status": VERIFIED, "ms": _ms(t0), "detail": "server keeps a tombstone for it"}
            continue
        if not present:
            continue
        differences = compare(obj) if compare else {}
        if not differences:
            return {**base, "status": VERIFIED, "ms": _ms(t0)}
        if i == len(delays):
            return {**base, "status": MISMATCH, "reason": "field_mismatch", "differences": differences, "ms": _ms(t0)}
    base = {"what": what, "compared": list(compared), "attempts": attempts, "ms": _ms(t0)}
    if last_reason == "read_failed":  # before the absent case: a read we cannot do proves nothing either way
        return {**base, "status": UNVERIFIED, "reason": "read_failed", "detail": last_error}
    if absent:
        return {**base, "status": MISMATCH, "reason": "still_present",
                "detail": "it is still there after the delete"}
    return {**base, "status": UNVERIFIED, "reason": "not_visible",
            "detail": last_error or "the re-read did not find it"}


def _ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)


def ok(v: Any) -> bool:
    return isinstance(v, dict) and v.get("status") == VERIFIED


def attach(payload: dict[str, Any], v: dict[str, Any]) -> dict[str, Any]:
    """Put the verdict on a write's result, and log the loud line when it is not proven."""
    out = {**payload, "verified": ok(v), "verification": v}
    if not ok(v):
        log.warning("unverified external write: %s", summary_text(v))
    return out


def summary_text(v: Any) -> str:
    """One human sentence for the UI and the log. Says what was compared and what went wrong."""
    if not isinstance(v, dict):
        return "This write was not verified."
    what, tries = v.get("what") or "the write", v.get("attempts") or 1
    if v.get("status") == VERIFIED:
        return f"Verified: {what}" + (f" ({', '.join(v['compared'])} match)" if v.get("compared") else "")
    if v.get("status") == MISMATCH and v.get("reason") == "still_present":
        return f"It may not have been deleted: {what} — {v.get('detail') or 'it is still there'}."
    if v.get("status") == MISMATCH:
        fields = ", ".join((v.get("differences") or {}).keys()) or "a field"
        return f"Stored differently than requested: {what} disagrees on {fields}."
    if v.get("reason") == "read_failed":
        return f"Could not confirm it: reading {what} back failed after {tries} tries ({v.get('detail')})."
    return (f"Could not confirm it: {what} was not there after {tries} read-backs. The write may "
            "not have happened at all.")


def tool_error_text(name: str, v: Any) -> str:
    """What the model is told. It must not be able to report success off its own request."""
    return (f"{name}: UNVERIFIED — {summary_text(v)} Do not tell the user this succeeded. Say it could "
            "not be confirmed and that they should check it themselves.")
