"""Google API clients are per thread: one shared httplib2 connection across worker threads let two
concurrent calls interleave on a socket, and one of them hung until the read timed out. Offline."""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import googleapiclient.discovery as discovery  # noqa: E402

from personal_os.google import Google  # noqa: E402


def main() -> None:
    built: list[Any] = []

    def fake_build(name: str, version: str, credentials: Any = None, cache_discovery: bool = False) -> Any:
        client = object()
        built.append(client)
        return client

    g = Google(lambda: {}, lambda _p: None)
    creds = object()
    g._creds = lambda: creds  # type: ignore[method-assign]
    real = discovery.build
    discovery.build = fake_build  # _svc imports build from the module at call time
    try:
        a1 = g._svc("gmail", "v1")
        a2 = g._svc("gmail", "v1")
        assert a1 is a2, "one thread reuses its client"
        other: dict[str, Any] = {}
        t = threading.Thread(target=lambda: other.update(c=g._svc("gmail", "v1"), again=g._svc("gmail", "v1")))
        t.start()
        t.join()
        assert other["c"] is not a1, "another thread gets its own client, never the shared connection"
        assert other["c"] is other["again"], "and reuses it"
        assert len(built) == 2, f"two clients built, got {len(built)}"
        g._creds = lambda: object()  # type: ignore[method-assign]  # a token refresh mints new credentials
        assert g._svc("gmail", "v1") is not a1, "new credentials rebuild this thread's client"
    finally:
        discovery.build = real
    print("test_google_threads: ok")


if __name__ == "__main__":
    main()
