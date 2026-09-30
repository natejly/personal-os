"""Adversarial tests for the fetch_url SSRF guard (no network: _check_url only, plus a stubbed resolver)."""
from __future__ import annotations

import os
import asyncio
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os.tools import UrlBlocked, _as_ip, _check_url, _ip_reason, _resolve  # noqa: E402

CTX: dict = {}
SET: dict = {}


def blocked(url: str, ctx: dict | None = None, settings: dict | None = None) -> str | None:
    try:
        _check_url(url, ctx if ctx is not None else CTX, settings if settings is not None else SET)
        return None
    except UrlBlocked as e:
        return str(e)


# ---- scheme / shape ----
def test_non_http_schemes_rejected():
    for u in ("file:///etc/passwd", "ftp://example.com/x", "gopher://example.com/", "data:text/html,x",
              "javascript:alert(1)", "example.com/path", "//example.com/path", ""):
        assert blocked(u), f"{u!r} was accepted"


def test_credentials_rejected():
    assert blocked("http://user:pw@example.com/")
    assert blocked("https://admin@169.254.169.254/")


# ---- IP literals ----
def test_loopback_and_friends_rejected():
    for u in ("http://127.0.0.1/", "http://0.0.0.0/", "http://[::1]/",
              "http://[::ffff:127.0.0.1]/", "http://[::]/", "http://169.254.169.254/latest/meta-data/",
              "http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.1/", "http://100.64.0.1/",
              "http://[fd00::1]/", "http://[fe80::1]/", "http://224.0.0.1/", "http://[2002:7f00:1::]/"):
        assert blocked(u), f"{u!r} was accepted"


def test_public_ip_and_host_accepted():
    assert blocked("http://93.184.216.34/") is None
    assert blocked("https://example.com/a/b") is None


# ---- alternate encodings: _check_url lets them through as hostnames; _resolve must catch them ----
def guard(url: str, ctx: dict | None = None, settings: dict | None = None) -> str | None:
    """The full guard as fetch_url runs it: _check_url then _resolve."""
    try:
        u, host = _check_url(url, ctx if ctx is not None else CTX, settings if settings is not None else SET)
        asyncio.run(_resolve(host))
        return None
    except UrlBlocked as e:
        return str(e)


def test_alt_encoded_ips_are_not_ip_literals():
    for host in ("2130706433", "0177.0.0.1", "0x7f000001", "127.1"):
        assert _as_ip(host) is None, f"{host} unexpectedly parsed as an IP literal"


def test_full_guard_blocks_alt_encoded_loopback():
    for u in ("http://2130706433/", "http://0x7f000001/", "http://127.1/", "http://127.0.1/",
              "http://017700000001/", "http://localhost/", "http://localhost./"):
        assert guard(u), f"{u!r} was accepted by the full guard"


def test_resolver_fails_closed_on_unparseable_addrinfo():
    """An address _as_ip cannot parse is an address we cannot judge, so it must be refused."""
    orig = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("not-an-ip", 0))]
    try:
        assert guard("http://whatever.test/")
    finally:
        socket.getaddrinfo = orig


# ---- taint overlay: a tainted reply may only re-fetch a URL it did not author ----
def tainted_ctx(urls: set[str] | None = None) -> dict:
    return {"tainted": True, "allowed_urls": urls or set()}


def test_tainted_allows_only_the_exact_urls_the_user_or_a_search_supplied():
    c = tainted_ctx({"https://example.com/page", "https://www.youtube.com/watch?v=abc"})
    assert blocked("https://example.com/page", c) is None
    assert blocked("https://example.com/page/", c) is None          # trailing slash is the same resource
    assert blocked("https://EXAMPLE.com/page", c) is None           # host case is not data
    assert blocked("https://www.youtube.com/watch?v=abc", c) is None  # a search result's query string must survive
    assert blocked("https://example.com/other", c)
    assert blocked("https://example.com/page?leak=secret", c)


def test_tainted_blocks_path_and_subdomain_exfiltration_to_a_surfaced_host():
    """The headline lethal-trifecta case: the attacker's own host is 'known', the payload rides in the path/label."""
    c = tainted_ctx({"https://notes.attacker.test/page"})
    assert blocked("https://notes.attacker.test/collect/dXNlci1zc24", c)
    assert blocked("https://dXNlci1zc24.notes.attacker.test/page", c)
    assert blocked("https://notes.attacker.test/page?x=dXNlci1zc24", c)


def test_tainted_honours_the_user_configured_host_allowlist():
    assert blocked("https://other.test/p?x=1", tainted_ctx(), {"fetchAllowlist": ["other.test"]}) is None
    assert blocked("https://docs.other.test/p", tainted_ctx(), {"fetchAllowlist": ["other.test"]}) is None
    assert blocked("https://notother.test/p", tainted_ctx(), {"fetchAllowlist": ["other.test"]})
    assert blocked("https://other.test.evil.test/p", tainted_ctx(), {"fetchAllowlist": ["other.test"]})


def test_tainted_redirect_hops_are_not_re_authorised():
    """A redirect target is chosen by the server, not the model, so it carries no model-authored bytes."""
    c = tainted_ctx({"https://example.com/page"})
    assert blocked("https://cdn.example.net/moved", c)
    try:
        _check_url("https://cdn.example.net/moved", c, SET, redirect=True)
    except UrlBlocked as e:
        raise AssertionError(f"redirect hop refused: {e}")


def _run() -> int:
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    print(f"\n{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_run())
