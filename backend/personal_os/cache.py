"""A small in-process TTL cache for read calls to slow remote APIs.

Google's APIs are the motivating case: opening the calendar fans out to one
`events.list` per calendar, and every view mount, week step and widget refresh
repeats the whole fan-out. The data barely changes in that window, so a short
TTL turns most of those round-trips into dictionary lookups.

Three rules keep it honest:

* **Reads only.** Writes are never cached; each one invalidates the namespaces
  it could have changed, so the next read sees the new state immediately.
* **Copies in, copies out.** Callers get a deep copy, so a caller that mutates
  its result cannot poison the entry for the next one.
* **Bypassable.** `with bypass():` serves a request straight from the API, which
  is what a user-initiated "refresh" should do.

Nothing here is persisted: a backend restart starts this layer cold on purpose.
Snapshots of calendar rows and mail headers live in google_store.py instead, so
the next read can ask Google for what changed. Disconnect and Google.forget drop
those too.
"""
from __future__ import annotations

import contextlib
import copy
import functools
import json
import logging
import threading
import time
from typing import Any, Callable, Iterator, TypeVar

log = logging.getLogger(__name__)

# Entries are small (event lists, message metadata), so the cap is about bounding
# pathological key churn - a tool loop reading hundreds of distinct Drive files -
# rather than memory.
MAX_ENTRIES = 1024

_local = threading.local()


@contextlib.contextmanager
def bypass() -> Iterator[None]:
    """Within this block, cached reads go to the API and refill the entry."""
    prev = getattr(_local, "bypass", False)
    _local.bypass = True
    try:
        yield
    finally:
        _local.bypass = prev


def bypassing() -> bool:
    return bool(getattr(_local, "bypass", False))


def dont_cache() -> None:
    """Called from inside a cached read: this result is incomplete, return it but do not store it."""
    _local.dont_cache = True


class TTLCache:
    """Namespaced TTL cache. Keys are `namespace:fingerprint-of-arguments`."""

    def __init__(self, max_entries: int = MAX_ENTRIES):
        self._lock = threading.Lock()
        self._data: dict[str, tuple[float, Any]] = {}
        self._max = max_entries
        # Bumped by invalidate: a read that started before a write must not store what it saw.
        self._gens: dict[str, int] = {}
        self._gen_all = 0
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> tuple[bool, Any]:
        """(hit, value). The flag matters because a cached value may be None."""
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return False, None
            expires, value = entry
            if expires <= now:
                del self._data[key]
                self.misses += 1
                return False, None
            # Refresh insertion order so eviction drops the least recently used.
            del self._data[key]
            self._data[key] = entry
            self.hits += 1
        return True, copy.deepcopy(value)

    def generation(self, namespace: str) -> tuple[int, int]:
        with self._lock:
            return self._gen_all, self._gens.get(namespace, 0)

    def put(self, key: str, value: Any, ttl: float, gen: tuple[int, int] | None = None) -> None:
        """Store `value`; with `gen` (from generation() before the read), only if nothing invalidated since."""
        if ttl <= 0:
            return
        with self._lock:
            if gen is not None and gen != (self._gen_all, self._gens.get(key.split(":", 1)[0], 0)):
                return
            self._data.pop(key, None)
            self._data[key] = (time.monotonic() + ttl, copy.deepcopy(value))
            while len(self._data) > self._max:
                self._data.pop(next(iter(self._data)))

    def invalidate(self, *namespaces: str) -> int:
        """Drop every entry in these namespaces; no arguments drops everything."""
        with self._lock:
            if not namespaces:
                self._gen_all += 1
                n = len(self._data)
                self._data.clear()
                return n
            for ns in namespaces:
                self._gens[ns] = self._gens.get(ns, 0) + 1
            prefixes = tuple(f"{ns}:" for ns in namespaces)
            dead = [k for k in self._data if k.startswith(prefixes)]
            for k in dead:
                del self._data[k]
            return len(dead)

    def clear(self) -> int:
        return self.invalidate()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            live = sum(1 for expires, _ in self._data.values() if expires > now)
            by_ns: dict[str, int] = {}
            for key, (expires, _) in self._data.items():
                if expires > now:
                    ns = key.split(":", 1)[0]
                    by_ns[ns] = by_ns.get(ns, 0) + 1
            total = self.hits + self.misses
            return {
                "entries": live,
                "namespaces": dict(sorted(by_ns.items())),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else None,
            }


def fingerprint(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """A stable, short key for a call's arguments."""
    try:
        raw = json.dumps([args, sorted(kwargs.items())], default=repr, sort_keys=True)
    except (TypeError, ValueError):  # pragma: no cover - default=repr covers almost everything
        raw = repr((args, sorted(kwargs.items())))
    # Long Drive queries and id lists would otherwise make unwieldy keys.
    return raw if len(raw) <= 160 else f"{raw[:120]}#{hash(raw):x}"


F = TypeVar("F", bound=Callable[..., Any])


def cached(namespace: str, ttl: float) -> Callable[[F], F]:
    """Cache a method's result for `ttl` seconds under `namespace`.

    `self` must expose a `_cache` TTLCache. Exceptions are never cached, so a
    transient Google 503 does not stick around for the rest of the TTL.
    """

    def wrap(fn: F) -> F:
        @functools.wraps(fn)
        def inner(self: Any, *args: Any, **kwargs: Any) -> Any:
            store: TTLCache | None = getattr(self, "_cache", None)
            if store is None:
                return fn(self, *args, **kwargs)
            key = f"{namespace}:{fn.__name__}:{fingerprint(args, kwargs)}"
            if not bypassing():
                hit, value = store.get(key)
                if hit:
                    return value
            gen = store.generation(namespace)
            outer = getattr(_local, "dont_cache", False)
            _local.dont_cache = False
            try:
                value = fn(self, *args, **kwargs)
                if not _local.dont_cache:
                    store.put(key, value, ttl, gen)
            finally:
                _local.dont_cache = outer or _local.dont_cache  # an incomplete inner read taints the outer one
            return value

        return inner  # type: ignore[return-value]

    return wrap


def invalidates(*namespaces: str) -> Callable[[F], F]:
    """Mark a write: on success it drops the cached reads it could have changed.

    Invalidating after the call (not before) means a failed write leaves the cache
    alone, and a successful one is visible to the very next read.
    """

    def wrap(fn: F) -> F:
        @functools.wraps(fn)
        def inner(self: Any, *args: Any, **kwargs: Any) -> Any:
            out = fn(self, *args, **kwargs)
            store: TTLCache | None = getattr(self, "_cache", None)
            if store is not None:
                store.invalidate(*namespaces)
            return out

        return inner  # type: ignore[return-value]

    return wrap
