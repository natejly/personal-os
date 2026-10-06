"""Approving a forced card for a tainted fetch lets that URL through; allow_host also keeps the host in fetchAllowlist.
Run: PYTHONPATH=<repo>/backend python backend/tests/test_fetch_allow_host.py"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="allowhost-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import app as appmod, tools  # noqa: E402

url = "https://docs.z.ai/guide?x=1"
ctx = {"tainted": True, "allowed_urls": set()}


def blocked(c: dict, cfg: dict) -> bool:
    try:
        tools._check_url(url, c, cfg)
        return False
    except tools.UrlBlocked as e:
        return True


assert blocked(ctx, {}), "tainted fetch of an unknown URL is refused"
appmod._approve_url(ctx, {"url": url}, False)
assert not blocked(ctx, {}), "approving the card lets that exact URL through"
assert appmod.settings().get("fetchAllowlist") == [] or "docs.z.ai" not in appmod.settings().get("fetchAllowlist")
assert blocked({"tainted": True, "allowed_urls": set()}, {}), "a plain approve does not keep the host"

appmod._approve_url({"allowed_urls": set()}, {"url": url}, True)
cfg = appmod.settings()
assert "docs.z.ai" in cfg["fetchAllowlist"], cfg["fetchAllowlist"]
assert not blocked({"tainted": True, "allowed_urls": set()}, cfg), "allowed host needs no card or approval later"
print("ok")
