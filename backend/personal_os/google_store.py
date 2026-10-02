"""Saved Google reads, so the next sync can ask for changes instead of every row.

The in-memory TTL cache (cache.py) still absorbs repeat calls within a minute.
This one outlives that, and a backend restart: a calendar window or a Gmail
header we have already seen is kept here, and the next read merges what Google
says changed. Nothing in this file is a credential.
"""
from __future__ import annotations

import copy
import json
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class ReadStore:
    """Namespaced JSON snapshots. `path` None keeps them in memory only (tests)."""

    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = {}
        self._dirty = False
        if path is not None and path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                log.warning("google read cache ignored: %s", e)
                loaded = {}
            if isinstance(loaded, dict):
                self._data = {k: v for k, v in loaded.items() if isinstance(v, dict)}

    def get(self, namespace: str, key: str) -> Any | None:
        with self._lock:
            bucket = self._data.get(namespace) or {}
            if key not in bucket:
                return None
            return copy.deepcopy(bucket[key])

    def put(self, namespace: str, key: str, value: Any, *, flush: bool = True, cap: int | None = None) -> None:
        """Save one value. With `cap`, the namespace keeps only its `cap` most recently saved keys."""
        with self._lock:
            bucket = self._data.setdefault(namespace, {})
            bucket.pop(key, None)  # re-saving moves the key to the young end
            bucket[key] = copy.deepcopy(value)
            while cap is not None and len(bucket) > max(1, cap):
                del bucket[next(iter(bucket))]
            self._dirty = True
            if flush:
                self._flush()

    def flush(self) -> None:
        with self._lock:
            self._flush()

    def clear(self, *namespaces: str) -> None:
        """Drop one namespace, or everything when called with no arguments."""
        with self._lock:
            if not namespaces:
                self._data.clear()
            else:
                for ns in namespaces:
                    self._data.pop(ns, None)
            self._dirty = True
            self._flush()

    def _flush(self) -> None:
        if self.path is None or not self._dirty:
            self._dirty = False
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._data), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.warning("google read cache not saved: %s", e)
        self._dirty = False
