"""The shell's way out: a local allowlisting proxy, the only address a sandboxed command may connect to.

Seatbelt can say "network or no network" and "this one address", not "these hostnames". So a sandboxed shell that
may reach a registry gets exactly one outbound destination, this proxy on 127.0.0.1, and the proxy does the name
policy. Each shell run carries a random token as proxy credentials (`http://grain:<token>@127.0.0.1:<port>`), which
is how a connection is tied to the run (and so to that run's allowed hosts) and how a run's `contacted` / `blocked`
lists are kept. A request without a live token is refused; a token is revoked when its run ends.

The Linux sandbox (microvm.py) runs this same file as a sidecar container: `sidecar()` binds every interface of a
container that sits on both the internet bridge and the sandbox's internal-only network, with one long-lived token,
and writes its contacted / blocked report to a file the host reads back. Binding beyond loopback changes nothing
about the checks below: no token, no tunnel, and IP literals and private addresses stay refused.

What it speaks: HTTP `CONNECT host:port` tunnels and plain-HTTP absolute-URI requests. No TLS interception (the
tunnel is opaque bytes, so nothing here can read or alter what a command sends), no SOCKS. Only ports 80 and 443.

The checks that matter, in order: the name must match the run's allowed set (exact host or a subdomain on a dot
boundary); it must not be an IP literal; every address it resolves to must be a public one (no loopback, private,
link-local, CGNAT, multicast, reserved, or the cloud metadata address); and the connection goes to the address that
was checked, never to a second resolution of the name (a rebinding answer cannot swap in an internal address).
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import os
import secrets
import socket
from dataclasses import dataclass, field
from typing import Any

REGISTRY_HOSTS = ("pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "crates.io", "static.crates.io", "index.crates.io",
                  "proxy.golang.org", "sum.golang.org", "rubygems.org", "index.rubygems.org")
PORTS = (80, 443)
MAX_TUNNELS_PER_TOKEN = 16
IDLE_S = 60.0                  # a tunnel or request with no bytes either way for this long is closed
HEAD_TIMEOUT_S = 15.0
CONNECT_TIMEOUT_S = 15.0
MAX_HEAD = 64 * 1024
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_METADATA = ("169.254.169.254", "fd00:ec2::254", "100.100.100.200")


# ---- policy (pure) ----
def normalize_host(h: str) -> str:
    return str(h or "").strip().lower().rstrip(".")


def _is_ip(h: str) -> bool:
    try:
        ipaddress.ip_address(h.strip("[]"))
        return True
    except ValueError:
        return False


def normalize_entry(e: Any) -> str | None:
    """A settings entry as a bare lowercase hostname, or None when it is not one (an IP, a wildcard, a URL, junk)."""
    if not isinstance(e, str):
        return None
    h = normalize_host(e)
    if not h or any(c in h for c in "*/:@?#%\\ ") or _is_ip(h) or "." not in h:
        return None
    if not all(p and all(c.isalnum() or c in "-_" for c in p) for p in h.split(".")):
        return None
    return h


def allowed_set(registry: bool, extra: Any, everything: bool = False) -> tuple[str, ...]:
    """The host entries a run may reach: the registry preset (when on) plus the user's valid extra entries.
    `everything` (allowAllConnections) is the one entry "*": any hostname, still never an IP literal."""
    if everything:
        return ("*",)
    out: list[str] = list(REGISTRY_HOSTS) if registry else []
    for e in extra if isinstance(extra, (list, tuple)) else []:
        n = normalize_entry(e)
        if n and n not in out:
            out.append(n)
    return tuple(out)


def host_allowed(host: str, entries: tuple[str, ...] | list[str]) -> bool:
    """Exact match or a subdomain on a dot boundary: `example.com` allows `api.example.com`, never `badexample.com`."""
    h = normalize_host(host)
    if not h or _is_ip(h):
        return False
    return any(e == "*" or h == e or h.endswith("." + e) for e in entries)


def addr_ok(ip: str) -> bool:
    """True only for a public unicast address. Anything that could be this machine or its network is refused."""
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    if a.version == 4 and a in _CGNAT:
        return False
    if str(a) in _METADATA:
        return False
    return not (a.is_loopback or a.is_private or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified
                or not a.is_global)


# ---- network seams (module level so tests can replace them) ----
async def resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    out: list[str] = []
    for _fam, _t, _p, _c, sa in infos:
        if sa[0] not in out:
            out.append(sa[0])
    return out


async def connect(addr: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.wait_for(asyncio.open_connection(addr, port), CONNECT_TIMEOUT_S)


@dataclass
class Run:
    allowed: tuple[str, ...]
    contacted: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    tunnels: int = 0
    log: str = ""   # the sidecar's report file; written before the client hears back, so a reader never misses a host

    def note(self, lst: list[str], host: str) -> None:
        if host not in lst:
            lst.append(host)
            if self.log:
                with open(self.log + ".tmp", "w") as f:
                    json.dump({"contacted": self.contacted, "blocked": self.blocked}, f)
                os.replace(self.log + ".tmp", self.log)


class Denied(Exception):
    pass


def proxy_env(host: str, port: int | None, token: str) -> dict[str, str]:
    """What a command needs to use the proxy. ALL_PROXY stays unset (no SOCKS here), NO_PROXY is empty so nothing bypasses."""
    url = f"http://grain:{token}@{host}:{port}"
    return {"HTTP_PROXY": url, "HTTPS_PROXY": url, "http_proxy": url, "https_proxy": url, "NO_PROXY": "", "no_proxy": "",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1"}


class Egress:
    def __init__(self, bind: str = "127.0.0.1", port: int = 0) -> None:
        self.runs: dict[str, Run] = {}
        self.bind, self._want_port = bind, port
        self.port: int | None = None
        self.idle_s = IDLE_S
        self._server: asyncio.AbstractServer | None = None
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._conns: set[asyncio.Task] = set()

    # -- lifecycle --
    async def ensure(self) -> int:
        """Start on first use; returns the port. A second caller waits for the first rather than binding twice."""
        loop = asyncio.get_running_loop()
        if self._loop is not loop:  # the app has one loop; a test's asyncio.run per call does not, and a server dies with its loop
            self._loop, self._lock, self._server, self._conns = loop, asyncio.Lock(), None, set()
        assert self._lock is not None
        async with self._lock:
            if self._server is None:
                self._server = await asyncio.start_server(self._client, self.bind, self._want_port)
                self.port = self._server.sockets[0].getsockname()[1]
        return int(self.port or 0)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
            self.port = None
        for t in list(self._conns):
            t.cancel()
        self.runs.clear()

    # -- per-run tokens --
    def new_run(self, allowed: tuple[str, ...] | list[str]) -> str:
        token = secrets.token_urlsafe(18)
        self.runs[token] = Run(tuple(allowed))
        return token

    def revoke(self, token: str | None) -> dict[str, list[str]]:
        """End a run's access and hand back what it did. Open tunnels of that token are closed by their next idle check."""
        r = self.runs.pop(token or "", None)
        return {"contacted": list(r.contacted), "blocked": list(r.blocked)} if r else {"contacted": [], "blocked": []}

    def stats(self, token: str | None) -> dict[str, list[str]]:
        r = self.runs.get(token or "")
        return {"contacted": list(r.contacted), "blocked": list(r.blocked)} if r else {"contacted": [], "blocked": []}

    def env(self, token: str) -> dict[str, str]:
        return proxy_env(self.bind, self.port, token)

    # -- the proxy itself --
    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task:
            self._conns.add(task)
        try:
            await self._serve(reader, writer)
        except (asyncio.CancelledError, ConnectionError, OSError, asyncio.IncompleteReadError):
            pass
        except Exception:  # noqa: BLE001 - one bad client must not take the proxy down
            pass
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass
            if task:
                self._conns.discard(task)

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, status: str, body: str, extra: str = "") -> None:
        data = (body + "\n").encode()
        writer.write(f"HTTP/1.1 {status}\r\n{extra}Content-Type: text/plain\r\nContent-Length: {len(data)}\r\n"
                     f"Connection: close\r\n\r\n".encode() + data)
        try:
            await writer.drain()
        except ConnectionError:
            pass

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_S)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            return
        if len(head) > MAX_HEAD:
            return
        lines = head[:-4].decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3:
            return await self._reply(writer, "400 Bad Request", "Bad request.")
        method, target, version = parts
        headers = [ln for ln in lines[1:] if ":" in ln]
        token = self._token(headers)
        run = self.runs.get(token or "")
        if run is None:
            return await self._reply(writer, "407 Proxy Authentication Required", "No live run token on this connection.",
                                     'Proxy-Authenticate: Basic realm="grain"\r\n')
        if method.upper() == "CONNECT":
            host, _, p = target.rpartition(":")
            host, port = host.strip("[]"), int(p) if p.isdigit() else 0
        else:
            if not target.lower().startswith("http://"):
                return await self._reply(writer, "400 Bad Request", "Only absolute http:// requests or CONNECT are proxied.")
            rest = target[7:]
            authority, slash, path = rest.partition("/")
            path = "/" + path if slash else "/"
            if "@" in authority:
                authority = authority.rsplit("@", 1)[1]
            if authority.startswith("["):
                host, _, tail = authority[1:].partition("]")
                port = int(tail[1:]) if tail.startswith(":") and tail[1:].isdigit() else 80
            else:
                host, _, p = authority.partition(":")
                port = int(p) if p.isdigit() else 80
        host = normalize_host(host)
        try:
            ip = await self._vet(run, host, port)
        except Denied as e:
            run.note(run.blocked, host or "?")
            return await self._reply(writer, "403 Forbidden", str(e))
        if run.tunnels >= MAX_TUNNELS_PER_TOKEN:
            return await self._reply(writer, "429 Too Many Requests", f"Too many open connections for this command (max {MAX_TUNNELS_PER_TOKEN}).")
        run.tunnels += 1
        try:
            try:
                up_r, up_w = await connect(ip, port)
            except (OSError, asyncio.TimeoutError) as e:
                return await self._reply(writer, "502 Bad Gateway", f"Could not reach {host}:{port} ({type(e).__name__}).")
            run.note(run.contacted, host)
            try:
                if method.upper() == "CONNECT":
                    writer.write(f"{version} 200 Connection established\r\n\r\n".encode())
                    await writer.drain()
                else:
                    kept = [ln for ln in headers if ln.split(":", 1)[0].strip().lower() not in
                            ("proxy-authorization", "proxy-connection", "connection")]
                    up_w.write((f"{method} {path} {version}\r\n" + "\r\n".join(kept + ["Connection: close"]) + "\r\n\r\n").encode("latin-1"))
                    await up_w.drain()
                await self._pipe(reader, writer, up_r, up_w, token or "")
            finally:
                up_w.close()
        finally:
            run.tunnels -= 1

    @staticmethod
    def _token(headers: list[str]) -> str | None:
        for ln in headers:
            k, _, v = ln.partition(":")
            if k.strip().lower() == "proxy-authorization":
                scheme, _, cred = v.strip().partition(" ")
                if scheme.lower() == "basic":
                    try:
                        user, _, pw = base64.b64decode(cred.strip()).decode("utf-8", "replace").partition(":")
                    except ValueError:
                        return None
                    return pw if user == "grain" else None
        return None

    async def _vet(self, run: Run, host: str, port: int) -> str:
        """The address to connect to, or Denied with the one line the client sees."""
        if not host:
            raise Denied("Blocked: no host.")
        if _is_ip(host):
            raise Denied(f"Blocked: {host} is an IP address; use its hostname.")
        if port not in PORTS:
            raise Denied(f"Blocked: {host}:{port} - only ports 80 and 443 are allowed.")
        if not host_allowed(host, run.allowed):
            raise Denied(f"Blocked: {host} is not on this command's allowed host list.")
        try:
            addrs = await resolve(host, port)
        except (OSError, asyncio.TimeoutError):
            raise Denied(f"Blocked: {host} did not resolve.") from None
        if not addrs:
            raise Denied(f"Blocked: {host} did not resolve.")
        for a in addrs:  # every answer must be public: one internal address among them is a rebinding attempt
            if not addr_ok(a):
                raise Denied(f"Blocked: {host} resolves to a private or local address.")
        return addrs[0]

    async def _pipe(self, cr: asyncio.StreamReader, cw: asyncio.StreamWriter, ur: asyncio.StreamReader,
                    uw: asyncio.StreamWriter, token: str) -> None:
        """Copy both ways until either side closes, the token is revoked, or nothing moves for idle_s."""
        last = [asyncio.get_running_loop().time()]

        async def copy(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            while True:
                chunk = await src.read(65536)
                if not chunk:
                    return
                last[0] = asyncio.get_running_loop().time()
                dst.write(chunk)
                await dst.drain()

        tasks = [asyncio.create_task(copy(cr, uw)), asyncio.create_task(copy(ur, cw))]
        try:
            while not any(t.done() for t in tasks):
                await asyncio.wait(tasks, timeout=min(1.0, self.idle_s), return_when=asyncio.FIRST_COMPLETED)
                if token not in self.runs or asyncio.get_running_loop().time() - last[0] > self.idle_s:
                    break
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def sidecar() -> None:
    """Entry point inside a sandbox's proxy container (`python3 -c <this file>`): one token, one allowed list, and a
    report file that survives a container stop/start, so a restart never forgets a host the sandbox already reached."""
    log = os.environ.get("GRAIN_PROXY_LOG") or "/tmp/egress.json"
    run = Run(tuple(h for h in os.environ.get("GRAIN_PROXY_ALLOW", "").split(",") if h), log=log)
    try:
        with open(log) as f:
            seen = json.load(f)
        run.contacted, run.blocked = [str(h) for h in seen["contacted"]], [str(h) for h in seen["blocked"]]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    eg = Egress("0.0.0.0", int(os.environ.get("GRAIN_PROXY_PORT") or 3128))
    eg.runs[os.environ["GRAIN_PROXY_TOKEN"]] = run

    async def main() -> None:
        await eg.ensure()
        await asyncio.Event().wait()
    asyncio.run(main())


if __name__ == "__main__":
    sidecar()
