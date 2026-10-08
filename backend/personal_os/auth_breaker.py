"""Provider auth breaker: stop sending requests with an API key the provider already rejected.

Keyed by (base URL, key fingerprint). The fingerprint is a short hash of the key; the key itself is never
stored, logged or put in an event. After FAILS_TO_OPEN key rejections in a row within WINDOW_S the breaker
opens: every chat-completion request with that base URL and key (replies, workers, desks, jobs, titles and
other helpers all go through llm._send_attempts) fails at once with `message()`, before any HTTP call.

It closes when:
  - a request with that key succeeds;
  - the key or base URL changes (a new fingerprint has no entry; PUT /settings also calls reset());
  - the user resets it (POST /provider/auth/reset);
  - COOLDOWN_S passes: then ONE request goes through as a probe. Success closes it; another rejection restarts
    the cooldown.

Only a key rejection counts: 401, a 400 from a LiteLLM proxy that does not know the key, or a 403 whose message
says the key is invalid or missing. A 403 for a model the account cannot use, 429, 5xx and network errors never
count; those keep llm's retry with backoff. This is a circuit breaker for a dead key, not a budget: nothing here
limits how many runs or rounds a working key may make.

State lives in memory and resets on restart, which costs at most FAILS_TO_OPEN failed requests.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any, Callable

from . import providers

log = logging.getLogger(__name__)

FAILS_TO_OPEN = 2
WINDOW_S = 600.0
COOLDOWN_S = 900.0

# A 403 counts only when it is about the key, not about what the key may do.
_KEY_MSG = re.compile(r"invalid (api )?key|api key|incorrect (api )?key|missing (api )?key|no api key|unauthenticated"
                      r"|authentication|invalid[_ ]token|invalid bearer|no connected db")

clock: Callable[[], float] = time.monotonic
_state: dict[tuple[str, str], dict[str, Any]] = {}
_listeners: list[Callable[[dict[str, Any]], None]] = []


def fingerprint(key: str | None) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12] if key else "-"


def _id(settings: dict[str, Any]) -> tuple[str, str]:
    return str(settings.get("baseUrl") or "").strip().rstrip("/"), fingerprint(settings.get("apiKey"))


def provider_name(settings: dict[str, Any]) -> str:
    p = providers.get(providers.effective(settings))
    return p["name"] if p and p["id"] != "custom" else "model provider"


def message(settings: dict[str, Any]) -> str:
    return f"The {provider_name(settings)} API key was rejected. Fix it in Settings → Model."


def is_key_rejection(status: int | None, body: str) -> bool:
    try:
        err = json.loads(body)
        err = err.get("error", err) if isinstance(err, dict) else err
        text = json.dumps(err).lower()
    except (ValueError, TypeError):
        text = str(body or "").lower()
    if status == 401:
        return True
    if status in (400, 403):
        return bool(_KEY_MSG.search(text)) and "model" not in text.replace("model provider", "")
    return False


def on_change(fn: Callable[[dict[str, Any]], None]) -> None:
    """fn({"provider", "open", "message"}) when a breaker opens or closes (once per transition, not per run)."""
    _listeners.append(fn)


def _emit(settings: dict[str, Any], is_open: bool) -> None:
    ev = {"provider": provider_name(settings), "open": is_open, "message": message(settings) if is_open else ""}
    for fn in list(_listeners):
        try:
            fn(ev)
        except Exception:  # noqa: BLE001 - a listener must never fail the request
            log.exception("auth breaker listener failed")


def blocked(settings: dict[str, Any]) -> str | None:
    """The fail-fast message while open and cooling down, else None. Does not use up the probe."""
    s = _state.get(_id(settings))
    if s and s.get("open_at") is not None and clock() - s["open_at"] < COOLDOWN_S:
        return message(settings)
    return None


def check(settings: dict[str, Any]) -> str | None:
    """Before a request: the message to fail with, or None to send it. Past the cooldown, the first caller is the
    probe and restarts the clock, so the callers behind it keep failing fast until the probe answers."""
    if (msg := blocked(settings)) is not None:
        return msg
    s = _state.get(_id(settings))
    if s and s.get("open_at") is not None:
        s["open_at"] = clock()
    return None


def failure(settings: dict[str, Any], status: int | None, body: str) -> None:
    """After a rejected request. Ignores anything that is not a key rejection."""
    if not is_key_rejection(status, body):
        return
    t, k = clock(), _id(settings)
    s = _state.get(k)
    if s is None or (s.get("open_at") is None and t - s["first"] > WINDOW_S):
        s = _state[k] = {"fails": 0, "first": t, "open_at": None}
    s["fails"] += 1
    if s["open_at"] is not None:
        s["open_at"] = t  # the probe was rejected too: another cooldown
    elif s["fails"] >= FAILS_TO_OPEN:
        s["open_at"] = t
        log.warning("%s rejected the API key %d times; holding requests with it", provider_name(settings), s["fails"])
        _emit(settings, True)


def success(settings: dict[str, Any]) -> None:
    s = _state.pop(_id(settings), None)
    if s and s.get("open_at") is not None:
        _emit(settings, False)


def reset(settings: dict[str, Any] | None = None) -> None:
    """Forget one provider+key (settings given) or everything. Closing an open breaker is announced."""
    if settings is not None:
        success(settings)
        return
    was_open = any(s.get("open_at") is not None for s in _state.values())
    _state.clear()
    if was_open:
        for fn in list(_listeners):
            try:
                fn({"provider": "", "open": False, "message": ""})
            except Exception:  # noqa: BLE001
                log.exception("auth breaker listener failed")
