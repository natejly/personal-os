"""No descriptor of the backend reaches a child it starts, and no connector child outlives the backend."""
from __future__ import annotations

import asyncio
import os
import signal
import socket
import sys
import tempfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="fdtest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from uvicorn import Config  # noqa: E402

from personal_os import app as app_mod  # noqa: E402
from personal_os import fdhygiene, mcp_client  # noqa: E402
from personal_os.serve import Server  # noqa: E402


def _listener() -> socket.socket:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    sock.set_inheritable(True)  # what a reload parent does to hand the socket to its worker
    return sock


async def _asgi(scope, receive, send) -> None:  # type: ignore[no-untyped-def]
    pass


def test_startup_makes_the_listening_socket_non_inheritable() -> None:
    sock = _listener()
    assert sock.get_inheritable()

    async def go() -> None:
        config = Config(_asgi, lifespan="off", log_config=None)
        config.load()
        server = Server(config)
        server.lifespan = config.lifespan_class(config)
        await server.startup(sockets=[sock])
        try:
            assert not sock.get_inheritable()
        finally:
            for s in server.servers:
                s.close()

    try:
        asyncio.run(go())
    finally:
        sock.close()


def test_seal_clears_every_inheritable_descriptor_above_stderr() -> None:
    r, w = os.pipe()
    os.set_inheritable(r, True)
    os.set_inheritable(w, True)
    try:
        changed = fdhygiene.seal()
        assert r in changed and w in changed
        assert not os.get_inheritable(r) and not os.get_inheritable(w)
        assert fdhygiene.seal() == []
    finally:
        os.close(r)
        os.close(w)


def test_seal_keeps_the_descriptors_it_is_told_to() -> None:
    r, w = os.pipe()
    os.set_inheritable(r, True)
    try:
        fdhygiene.seal(keep=[r])
        assert os.get_inheritable(r)
    finally:
        os.close(r)
        os.close(w)


def test_a_child_started_on_uvloop_inherits_nothing_after_seal() -> None:
    import pytest

    uvloop = pytest.importorskip("uvloop")
    sock = _listener()
    fd = sock.fileno()

    async def listing() -> set[int]:
        p = await asyncio.create_subprocess_exec("ls", "/proc/self/fd", stdout=asyncio.subprocess.PIPE, close_fds=True)
        out, _ = await p.communicate()
        return {int(x) for x in out.split()}

    try:
        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            assert fd in runner.run(listing())  # the leak this module exists for: close_fds is ignored
            fdhygiene.seal()
            assert fd not in runner.run(listing())
    finally:
        sock.close()


def test_stdio_spawn_seals_inheritable_descriptors_first() -> None:
    r, w = os.pipe()
    os.set_inheritable(r, True)
    seen: dict[str, bool] = {}

    class Fake:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            seen["inheritable"] = os.get_inheritable(r)
            return object(), object()

        async def __aexit__(self, *exc) -> None:  # type: ignore[no-untyped-def]
            return None

    config = mcp_client._Config(id="x", slug="x", name="x", transport="stdio", command=sys.executable, args=[], cwd="",
                                env={"PATH": os.environ.get("PATH", "/usr/bin")}, url="", headers={}, call_timeout=0)

    async def go() -> None:
        with mock.patch.object(mcp_client, "stdio_client", lambda *a, **k: Fake()):
            async with mcp_client._open(config, mock.Mock(file=sys.stderr), None):
                pass

    try:
        asyncio.run(go())
        assert seen == {"inheritable": False}
    finally:
        os.close(r)
        os.close(w)


class _Proc:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None


def test_reap_signals_the_group_of_a_server_still_running() -> None:
    proc = _Proc(424242)
    killed: list[tuple[int, int]] = []

    def killpg(pid: int, sig: int) -> None:
        killed.append((pid, sig))
        if sig == signal.SIGTERM:
            proc.returncode = -15

    mcp_client._SPAWNED[:] = [proc, _Proc(1)]
    mcp_client._SPAWNED[1].returncode = 0  # already gone: left alone
    with mock.patch.object(os, "killpg", killpg):
        assert mcp_client.reap_spawned(grace=0.5) == 0
    assert killed == [(424242, signal.SIGTERM)]
    assert mcp_client._SPAWNED == []


def test_reap_escalates_to_kill_when_a_server_ignores_terminate() -> None:
    proc = _Proc(424243)
    killed: list[int] = []

    def killpg(pid: int, sig: int) -> None:
        killed.append(sig)
        if sig == signal.SIGKILL:
            proc.returncode = -9

    mcp_client._SPAWNED[:] = [proc]
    with mock.patch.object(os, "killpg", killpg):
        mcp_client.reap_spawned(grace=0.1)
    assert killed == [signal.SIGTERM, signal.SIGKILL]


def test_client_stop_reaps_what_the_transport_left_behind() -> None:
    client = mcp_client.McpClient.__new__(mcp_client.McpClient)
    client._supervisors = {}
    client.oauth = None
    with mock.patch.object(mcp_client, "reap_spawned") as reap:
        asyncio.run(client.stop())
    reap.assert_called_once()


def test_backend_shutdown_stops_the_connectors() -> None:
    calls: list[str] = []

    async def stop() -> None:
        calls.append("mcp")

    async def noop(*a, **k) -> None:  # type: ignore[no-untyped-def]
        return None

    async def go() -> None:
        with mock.patch.object(app_mod.mcp, "stop", stop), mock.patch.object(app_mod.workers_mgr, "live", lambda: []), \
                mock.patch.object(app_mod.coding, "shutdown", noop), mock.patch.object(app_mod.host.channels, "stop", noop), \
                mock.patch.object(app_mod.toolbox.shell, "shutdown", noop), mock.patch.object(app_mod.bus, "shutdown", noop), \
                mock.patch.object(app_mod.title_jobs, "stop", noop), mock.patch.object(app_mod.followup_jobs, "stop", noop), \
                mock.patch.object(app_mod.learner, "stop", noop), mock.patch.object(app_mod.llm, "aclose", noop):
            await app_mod._shutdown()

    asyncio.run(go())
    assert calls == ["mcp"]


def test_a_stuck_connector_stop_does_not_hold_up_shutdown() -> None:
    async def go() -> None:
        async def hang() -> None:
            await asyncio.sleep(30)

        await app_mod._bounded("mcp", hang(), 0.05)

    asyncio.run(go())
