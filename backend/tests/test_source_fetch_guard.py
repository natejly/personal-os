"""Regression tests for guarded_request: the SSRF guard must run on every redirect hop, and a hop that leaves the
original host must lose the credential headers. No network - the httpx client and the resolver are both stubbed."""
from __future__ import annotations

import asyncio
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import tools  # noqa: E402
from personal_os.tools import UrlBlocked, guarded_request  # noqa: E402


class FakeResponse:
    def __init__(self, url: str, status_code: int = 200, location: str | None = None):
        self.url = url
        self.status_code = status_code
        self.headers = {"location": location} if location else {}


class FakeClient:
    """Records every request it is asked to make and replays a scripted redirect chain."""

    def __init__(self, script: dict[str, tuple[int, str | None]] | None = None):
        self.script = script or {}
        self.calls: list[dict] = []

    async def request(self, method, url, headers=None, params=None, content=None, extensions=None):
        headers = dict(headers or {})
        logical = _logical(url, headers)
        self.calls.append({"method": method, "url": logical, "pinned": url, "headers": headers,
                           "params": params, "content": content, "extensions": dict(extensions or {})})
        status, location = self.script.get(logical, (200, None))
        return FakeResponse(logical, status, location)


def _logical(url: str, headers: dict) -> str:
    """The URL a caller asked for, recovered from the Host header when the socket was pinned to an address."""
    host = next((v for k, v in headers.items() if k.lower() == "host" and v), "")
    if not host:
        return url
    u = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((u.scheme, host, u.path, u.query, ""))


def _public_resolver(mapping: dict[str, str] | None = None):
    """Stub _resolve so no DNS happens: every host resolves to a public address unless mapped otherwise."""
    async def fake_resolve(host: str) -> list[str]:
        ip = (mapping or {}).get(host, "93.184.216.34")
        parsed = tools._as_ip(ip)
        if parsed is not None and (reason := tools._ip_reason(parsed)):
            raise UrlBlocked(f"{host} resolves to {ip}, which is {reason}")
        return [ip]
    return fake_resolve


def run(coro):
    return asyncio.run(coro)


def _with_resolver(resolver, fn):
    original = tools._resolve
    tools._resolve = resolver
    try:
        return fn()
    finally:
        tools._resolve = original


def blocked(fn) -> str | None:
    try:
        fn()
        return None
    except UrlBlocked as e:
        return str(e)


# ---- the first URL still gets checked ----
def test_loopback_target_is_refused():
    c = FakeClient()
    msg = _with_resolver(_public_resolver(), lambda: blocked(
        lambda: run(guarded_request(c, "GET", "http://127.0.0.1:8791/health"))))
    assert msg and "loopback" in msg, msg
    assert c.calls == [], "refused URL must never be connected"


def test_non_http_scheme_is_refused():
    c = FakeClient()
    msg = _with_resolver(_public_resolver(), lambda: blocked(
        lambda: run(guarded_request(c, "GET", "file:///etc/passwd"))))
    assert msg and "http" in msg, msg
    assert c.calls == []


def test_public_url_is_fetched():
    c = FakeClient()
    r = _with_resolver(_public_resolver(), lambda: run(guarded_request(c, "GET", "https://example.com/data.json")))
    assert r.status_code == 200
    assert [x["url"] for x in c.calls] == ["https://example.com/data.json"]


def test_the_connection_is_pinned_to_the_resolved_address():
    """A second lookup of the name must not be what the socket opens. That is the DNS-rebind window."""
    c = FakeClient()
    _with_resolver(_public_resolver(), lambda: run(guarded_request(c, "GET", "https://example.com/data.json")))
    call = c.calls[0]
    assert urllib.parse.urlsplit(call["pinned"]).hostname == "93.184.216.34"
    assert call["headers"]["Host"] == "example.com"
    assert call["extensions"]["sni_hostname"] == "example.com"


# ---- the bug: a public host redirecting inward ----
def test_redirect_into_loopback_is_refused():
    c = FakeClient({"https://example.com/start": (302, "http://127.0.0.1:8791/health")})
    msg = _with_resolver(_public_resolver(), lambda: blocked(
        lambda: run(guarded_request(c, "GET", "https://example.com/start"))))
    assert msg and "loopback" in msg, msg
    assert [x["url"] for x in c.calls] == ["https://example.com/start"], "the loopback hop must not be connected"


def test_redirect_into_sixtofour_loopback_is_refused():
    c = FakeClient({"https://example.com/start": (302, "http://[2002:7f00:1::]/")})
    msg = _with_resolver(_public_resolver(), lambda: blocked(
        lambda: run(guarded_request(c, "GET", "https://example.com/start"))))
    assert msg and "loopback" in msg, msg
    assert [x["url"] for x in c.calls] == ["https://example.com/start"]


def test_redirect_into_site_local_is_refused():
    c = FakeClient({"https://example.com/start": (302, "http://[fec0::1]/secret")})
    msg = _with_resolver(_public_resolver(), lambda: blocked(
        lambda: run(guarded_request(c, "GET", "https://example.com/start"))))
    assert msg and "private" in msg, msg
    assert [x["url"] for x in c.calls] == ["https://example.com/start"], "the site-local hop must not be connected"


def test_redirect_into_metadata_range_is_refused():
    c = FakeClient({"https://example.com/start": (302, "http://169.254.169.254/latest/meta-data/")})
    msg = _with_resolver(_public_resolver(), lambda: blocked(
        lambda: run(guarded_request(c, "GET", "https://example.com/start"))))
    assert msg and "link-local" in msg, msg


def test_redirect_to_a_host_resolving_privately_is_refused():
    c = FakeClient({"https://example.com/start": (302, "https://internal.example/secret")})
    resolver = _public_resolver({"internal.example": "10.0.0.5"})
    msg = _with_resolver(resolver, lambda: blocked(
        lambda: run(guarded_request(c, "GET", "https://example.com/start"))))
    assert msg and "10.0.0.5" in msg, msg


def test_redirect_chain_to_a_public_host_is_followed():
    c = FakeClient({"https://example.com/a": (302, "https://example.com/b"),
                    "https://example.com/b": (301, "https://cdn.example.org/c")})
    r = _with_resolver(_public_resolver(), lambda: run(guarded_request(c, "GET", "https://example.com/a")))
    assert r.url == "https://cdn.example.org/c"
    assert [x["url"] for x in c.calls] == ["https://example.com/a", "https://example.com/b", "https://cdn.example.org/c"]


def test_redirect_loop_stops():
    c = FakeClient({"https://example.com/loop": (302, "https://example.com/loop")})
    msg = _with_resolver(_public_resolver(), lambda: blocked(
        lambda: run(guarded_request(c, "GET", "https://example.com/loop", max_hops=3))))
    assert msg and "too many redirects" in msg, msg
    assert len(c.calls) == 4, c.calls


# ---- credentials must not follow the connection off-host ----
def test_secret_is_dropped_when_a_redirect_leaves_the_origin():
    c = FakeClient({"https://api.example.com/v1": (302, "https://evil.example.net/collect")})
    hdrs = {"Authorization": "Bearer super-secret", "Accept": "application/json"}
    _with_resolver(_public_resolver(), lambda: run(guarded_request(c, "GET", "https://api.example.com/v1", headers=hdrs)))
    first, second = c.calls[0], c.calls[1]
    assert first["headers"].get("Authorization") == "Bearer super-secret"
    assert "Authorization" not in second["headers"], second["headers"]
    assert second["headers"].get("Accept") == "application/json", "non-credential headers should survive"


def test_secret_survives_a_same_host_redirect():
    c = FakeClient({"https://api.example.com/v1": (302, "https://api.example.com/v2")})
    hdrs = {"Authorization": "Bearer super-secret"}
    _with_resolver(_public_resolver(), lambda: run(guarded_request(c, "GET", "https://api.example.com/v1", headers=hdrs)))
    assert c.calls[1]["headers"].get("Authorization") == "Bearer super-secret"


def test_cookie_and_api_key_headers_are_dropped_off_host():
    c = FakeClient({"https://api.example.com/v1": (302, "https://other.example.net/x")})
    hdrs = {"Cookie": "session=abc", "X-API-Key": "k", "Proxy-Authorization": "p"}
    _with_resolver(_public_resolver(), lambda: run(guarded_request(c, "GET", "https://api.example.com/v1", headers=hdrs)))
    leftover = {k: v for k, v in c.calls[1]["headers"].items() if k.lower() != "host"}
    assert leftover == {}, c.calls[1]["headers"]


# ---- request shape across hops ----
def test_query_params_are_not_reapplied_to_the_redirect_target():
    c = FakeClient({"https://example.com/a": (302, "https://example.com/b?page=2")})
    _with_resolver(_public_resolver(), lambda: run(
        guarded_request(c, "GET", "https://example.com/a", params={"api_key": "k"})))
    assert c.calls[0]["params"] == {"api_key": "k"}
    assert c.calls[1]["params"] is None, "the redirect target carries its own query"


def test_303_becomes_a_get_and_drops_the_body():
    c = FakeClient({"https://example.com/submit": (303, "https://example.com/result")})
    _with_resolver(_public_resolver(), lambda: run(
        guarded_request(c, "POST", "https://example.com/submit", content=b'{"a":1}')))
    assert c.calls[0]["method"] == "POST" and c.calls[0]["content"] == b'{"a":1}'
    assert c.calls[1]["method"] == "GET" and c.calls[1]["content"] is None


def test_307_keeps_the_method_and_body():
    c = FakeClient({"https://example.com/submit": (307, "https://example.com/again")})
    _with_resolver(_public_resolver(), lambda: run(
        guarded_request(c, "POST", "https://example.com/submit", content=b'{"a":1}')))
    assert c.calls[1]["method"] == "POST" and c.calls[1]["content"] == b'{"a":1}'
