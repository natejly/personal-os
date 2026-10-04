"""The allowlisting proxy (egress.py). Nothing here leaves the machine: the resolver and the connect step are replaced so
"the internet" is a server this test starts on 127.0.0.1, while the address policy itself is asserted for real."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
import time
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import egress  # noqa: E402

PUBLIC = "93.184.216.34"


def test_entry_normalisation_and_matching() -> None:
    assert egress.normalize_entry(" Example.COM. ") == "example.com"
    for bad in ("1.2.3.4", "*.example.com", "https://example.com", "example.com/x", "example.com:8080", "localhost", "", None, "::1", "a b.com"):
        assert egress.normalize_entry(bad) is None, bad
    allowed = egress.allowed_set(True, ["Example.com", "10.0.0.1", "*.bad.com", "example.com"])
    assert "pypi.org" in allowed and allowed.count("example.com") == 1 and "10.0.0.1" not in allowed
    assert egress.allowed_set(False, []) == ()
    e = ("example.com",)
    assert egress.host_allowed("example.com", e) and egress.host_allowed("API.Example.com.", e)
    assert egress.host_allowed("a.b.example.com", e)
    assert not egress.host_allowed("badexample.com", e) and not egress.host_allowed("example.com.evil.io", e)
    assert not egress.host_allowed("127.0.0.1", ("127.0.0.1",)) and not egress.host_allowed("", e)


def test_real_address_check_refuses_everything_internal() -> None:
    for ip in ("127.0.0.1", "::1", "10.1.2.3", "192.168.0.5", "172.16.0.1", "169.254.169.254", "100.64.0.1", "100.100.100.200",
               "224.0.0.1", "0.0.0.0", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "240.0.0.1", "garbage"):
        assert not egress.addr_ok(ip), ip
    assert egress.addr_ok(PUBLIC) and egress.addr_ok("2606:4700::1111")


class World:
    """A fake upstream plus a client helper, all on one loop."""

    def __init__(self) -> None:
        self.heads: list[bytes] = []
        self.upstream: asyncio.AbstractServer | None = None
        self.up_port = 0
        self.resolved: dict[str, list[str]] = {}
        self.connects: list[tuple[str, int]] = []

    async def start(self, mp: pytest.MonkeyPatch, bind: str = "127.0.0.1") -> egress.Egress:
        async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            data = await r.read(65536)
            self.heads.append(data)
            if data.startswith(b"GET "):
                w.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            else:
                w.write(b"pong:" + data)
            await w.drain()
            w.close()
        self.upstream = await asyncio.start_server(handle, "127.0.0.1", 0)
        self.up_port = self.upstream.sockets[0].getsockname()[1]

        async def fake_resolve(host: str, port: int) -> list[str]:
            return self.resolved.get(host, [PUBLIC])

        async def fake_connect(addr: str, port: int) -> Any:
            self.connects.append((addr, port))
            return await asyncio.open_connection("127.0.0.1", self.up_port)
        mp.setattr(egress, "resolve", fake_resolve)
        mp.setattr(egress, "connect", fake_connect)
        eg = egress.Egress(bind)
        await eg.ensure()
        return eg

    async def request(self, eg: egress.Egress, raw: str, token: str | None = None, send_after: bytes = b"") -> bytes:
        r, w = await asyncio.open_connection("127.0.0.1", eg.port)
        auth = f"Proxy-Authorization: Basic {base64.b64encode(f'grain:{token}'.encode()).decode()}\r\n" if token else ""
        w.write(raw.replace("\r\n\r\n", "\r\n" + auth + "\r\n", 1).encode())
        await w.drain()
        out = b""
        if send_after:
            out += await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)
            w.write(send_after)
            await w.drain()
        out += await asyncio.wait_for(r.read(65536), 5)
        w.close()
        return out

    async def close(self, eg: egress.Egress) -> None:
        await eg.close()
        assert self.upstream
        self.upstream.close()


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_connect_and_plain_http_through_the_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    async def go() -> None:
        w = World()
        eg = await w.start(monkeypatch)
        try:
            tok = eg.new_run(("example.com",))
            out = await w.request(eg, "CONNECT api.example.com:443 HTTP/1.1\r\nHost: api.example.com:443\r\n\r\n", tok, b"hello")
            assert out.startswith(b"HTTP/1.1 200") and out.endswith(b"pong:hello")
            out = await w.request(eg, "GET http://example.com/path?q=1 HTTP/1.1\r\nHost: example.com\r\nProxy-Connection: keep-alive\r\n\r\n", tok)
            assert out.startswith(b"HTTP/1.1 200") and out.endswith(b"ok")
            head = w.heads[-1].decode()
            assert head.startswith("GET /path?q=1 HTTP/1.1") and "Proxy-" not in head and "Connection: close" in head
            assert w.connects == [(PUBLIC, 443), (PUBLIC, 80)]  # connected to the address that was checked
            assert eg.stats(tok) == {"contacted": ["api.example.com", "example.com"], "blocked": []}
        finally:
            await w.close(eg)
    run(go())


def test_token_is_required_and_revoked(monkeypatch: pytest.MonkeyPatch) -> None:
    async def go() -> None:
        w = World()
        eg = await w.start(monkeypatch)
        try:
            req = "CONNECT example.com:443 HTTP/1.1\r\n\r\n"
            assert (await w.request(eg, req)).startswith(b"HTTP/1.1 407")
            assert (await w.request(eg, req, "not-a-token")).startswith(b"HTTP/1.1 407")
            tok = eg.new_run(("example.com",))
            assert (await w.request(eg, req, tok, b"x")).startswith(b"HTTP/1.1 200")
            seen = eg.revoke(tok)
            assert seen["contacted"] == ["example.com"]
            assert (await w.request(eg, req, tok)).startswith(b"HTTP/1.1 407")
            assert eg.stats(tok) == {"contacted": [], "blocked": []}
            assert w.connects == [(PUBLIC, 443)]
        finally:
            await w.close(eg)
    run(go())


def test_denials_are_403_recorded_and_never_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    async def go() -> None:
        w = World()
        eg = await w.start(monkeypatch)
        try:
            tok = eg.new_run(("example.com",))
            for host, port in (("badexample.com", 443), ("evil.io", 443), ("example.com", 8080), ("example.com", 22), ("127.0.0.1", 443),
                               ("93.184.216.34", 80)):
                out = await w.request(eg, f"CONNECT {host}:{port} HTTP/1.1\r\n\r\n", tok)
                assert out.startswith(b"HTTP/1.1 403") and host.encode() in out, (host, port, out)
            # an allowed name that resolves to anything internal is refused too, for every answer it gives
            w.resolved["example.com"] = [PUBLIC, "10.0.0.7"]
            assert (await w.request(eg, "CONNECT example.com:443 HTTP/1.1\r\n\r\n", tok)).startswith(b"HTTP/1.1 403")
            w.resolved["example.com"] = ["127.0.0.1"]
            out = await w.request(eg, "GET http://example.com/ HTTP/1.1\r\n\r\n", tok)
            assert out.startswith(b"HTTP/1.1 403") and b"private or local" in out
            assert w.connects == []
            st = eg.stats(tok)
            assert st["contacted"] == [] and st["blocked"] == ["badexample.com", "evil.io", "example.com", "127.0.0.1", "93.184.216.34"]
        finally:
            await w.close(eg)
    run(go())


def test_accounting_is_per_token_and_the_tunnel_cap_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    async def go() -> None:
        w = World()
        eg = await w.start(monkeypatch)
        try:
            a, b = eg.new_run(("a.com",)), eg.new_run(("b.com",))
            await w.request(eg, "CONNECT a.com:443 HTTP/1.1\r\n\r\n", a, b"x")
            assert (await w.request(eg, "CONNECT a.com:443 HTTP/1.1\r\n\r\n", b)).startswith(b"HTTP/1.1 403")
            assert eg.stats(a) == {"contacted": ["a.com"], "blocked": []} and eg.stats(b) == {"contacted": [], "blocked": ["a.com"]}
            eg.runs[a].tunnels = egress.MAX_TUNNELS_PER_TOKEN
            assert (await w.request(eg, "CONNECT a.com:443 HTTP/1.1\r\n\r\n", a)).startswith(b"HTTP/1.1 429")
        finally:
            await w.close(eg)
    run(go())


def test_env_carries_the_token_and_leaves_socks_and_bypass_unset() -> None:
    eg = egress.Egress()
    eg.port = 4321
    env = eg.env("tok")
    assert env["HTTP_PROXY"] == env["https_proxy"] == "http://grain:tok@127.0.0.1:4321"
    assert env["NO_PROXY"] == "" and "ALL_PROXY" not in env and env["PIP_DISABLE_PIP_VERSION_CHECK"] == "1"


def test_one_server_is_started_lazily_and_closed() -> None:
    async def go() -> None:
        eg = egress.Egress()
        assert eg.port is None
        p1, p2 = await asyncio.gather(eg.ensure(), eg.ensure())
        assert p1 == p2 and p1 > 0
        await eg.close()
        assert eg.port is None
    run(go())


def test_a_listener_beyond_loopback_still_needs_the_token_and_refuses_internal_targets(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """The sandbox sidecar binds every interface. That must change nothing about who gets through or where to."""
    async def go() -> None:
        w = World()
        eg = await w.start(monkeypatch, bind="0.0.0.0")
        try:
            assert eg.env("t")["HTTP_PROXY"] == f"http://grain:t@0.0.0.0:{eg.port}"
            req = "CONNECT example.com:443 HTTP/1.1\r\n\r\n"
            assert (await w.request(eg, req)).startswith(b"HTTP/1.1 407")
            assert (await w.request(eg, req, "wrong")).startswith(b"HTTP/1.1 407")
            log = str(tmp_path / "egress.json")
            eg.runs["tok"] = egress.Run(("example.com",), log=log)
            for host in ("10.0.0.7", "127.0.0.1", "169.254.169.254"):
                assert (await w.request(eg, f"CONNECT {host}:443 HTTP/1.1\r\n\r\n", "tok")).startswith(b"HTTP/1.1 403")
            w.resolved["example.com"] = ["192.168.1.10"]
            assert (await w.request(eg, req, "tok")).startswith(b"HTTP/1.1 403")
            w.resolved["example.com"] = [PUBLIC]
            assert (await w.request(eg, req, "tok", b"x")).startswith(b"HTTP/1.1 200")
            assert w.connects == [(PUBLIC, 443)]
            with open(log) as f:  # the report the host reads back is on disk by the time the client got its answer
                assert json.load(f) == {"contacted": ["example.com"],
                                        "blocked": ["10.0.0.7", "127.0.0.1", "169.254.169.254", "example.com"]}
        finally:
            await w.close(eg)
    run(go())


def test_the_file_runs_as_the_sidecar_and_keeps_its_report_across_restarts(tmp_path: Any) -> None:
    """Exactly what the sandbox's proxy container runs: `python3 -c <egress.py>` with the token and allowlist in env."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = tmp_path / "egress.json"
    log.write_text(json.dumps({"contacted": ["pypi.org"], "blocked": []}))  # what it saw before a stop/start
    env = {**os.environ, "GRAIN_PROXY_TOKEN": "sekrit", "GRAIN_PROXY_ALLOW": "pypi.org,example.com",
           "GRAIN_PROXY_PORT": str(port), "GRAIN_PROXY_LOG": str(log)}
    with open(egress.__file__) as f:
        src = f.read()
    p = subprocess.Popen([sys.executable, "-c", src], env=env)

    def ask(raw: str) -> bytes:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as c:
            c.sendall(raw.encode())
            return c.recv(4096)
    try:
        for _ in range(100):
            try:
                ask("x\r\n\r\n")
                break
            except OSError:
                time.sleep(0.05)
        auth = f"Proxy-Authorization: Basic {base64.b64encode(b'grain:sekrit').decode()}\r\n"
        assert ask("CONNECT evil.io:443 HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 407")
        assert ask(f"CONNECT evil.io:443 HTTP/1.1\r\n{auth}\r\n").startswith(b"HTTP/1.1 403")
        assert ask(f"CONNECT 10.0.0.1:443 HTTP/1.1\r\n{auth}\r\n").startswith(b"HTTP/1.1 403")
        assert json.loads(log.read_text()) == {"contacted": ["pypi.org"], "blocked": ["evil.io", "10.0.0.1"]}
    finally:
        p.kill()
        p.wait()
