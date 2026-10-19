"""Extension dependencies: enabling one whose required extensions are off, and the helpers the catalog reads.

Run: python -m pytest backend/tests/test_extension_requires.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os.db import Database  # noqa: E402
from personal_os.extensions import review as review_mod  # noqa: E402
from personal_os.extensions.drafts import Drafts  # noqa: E402
from personal_os.extensions.host import ExtensionContext, ExtensionHost  # noqa: E402
from personal_os.extensions.manifest import Manifest  # noqa: E402


def build(specs: dict[str, dict[str, Any]], hidden: list[str]) -> tuple[ExtensionHost, review_mod.Review, TestClient]:
    data = tempfile.mkdtemp(prefix="extreq-")
    db = Database(data)
    db.set_settings({"hiddenViews": hidden})
    ctx = ExtensionContext(db=db, hidden_views=lambda: db.get_settings().get("hiddenViews") or (), services={}, settings=db.get_settings,
                           set_settings=db.set_settings, sid=lambda p: p, wsid=lambda p: p, publish=lambda _n, _d: None)
    host = ExtensionHost()
    for ext_id, extra in specs.items():
        host._add(Manifest.from_dict({"id": ext_id, "name": ext_id.title(), "version": "1.0.0", "apiVersion": 1, **extra}), ctx=ctx)
    review = review_mod.Review(Drafts(Path(data)), host, ctx, lambda: None)  # type: ignore[arg-type,return-value]
    app = FastAPI()
    app.include_router(review_mod.router(review))
    return host, review, TestClient(app)


CHAIN = {"a": {"requires": {"extensions": ["b"]}}, "b": {"requires": {"extensions": ["c"]}}, "c": {}}


def test_enabling_with_a_required_extension_off_enables_nothing_and_names_it() -> None:
    host, _, client = build(CHAIN, ["a", "b", "c"])
    out = client.post("/extensions/a/enable", json={}).json()
    assert out["ok"] is False and out["enabled"] == []
    assert out["needs"] == [{"id": "c", "name": "C", "enabled": False}, {"id": "b", "name": "B", "enabled": False}]
    assert not host.is_on("a") and not host.is_on("b") and not host.is_on("c")


def test_with_requires_enables_the_chain_in_dependency_order() -> None:
    host, _, client = build(CHAIN, ["a", "b", "c"])
    out = client.post("/extensions/a/enable", json={"with_requires": True}).json()
    assert out["ok"] is True and out["enabled"] == ["c", "b", "a"] and out["needs"] == []
    assert all(host.is_on(i) for i in "abc")


def test_a_required_extension_that_is_already_on_is_not_listed() -> None:
    host, _, client = build(CHAIN, ["a", "b"])
    out = client.post("/extensions/a/enable", json={"with_requires": True}).json()
    assert out["enabled"] == ["b", "a"]


def test_no_requirements_just_enables_the_target() -> None:
    host, _, client = build(CHAIN, ["c"])
    out = client.post("/extensions/c/enable").json()
    assert out["ok"] is True and out["enabled"] == ["c"] and host.is_on("c")


def test_a_dependency_cycle_terminates() -> None:
    specs = {"x": {"requires": {"extensions": ["y"]}}, "y": {"requires": {"extensions": ["x"]}}}
    host, _, client = build(specs, ["x", "y"])
    out = client.post("/extensions/x/enable", json={"with_requires": True}).json()
    assert out["ok"] is True and set(out["enabled"]) == {"x", "y"} and out["enabled"][-1] == "x"


def test_the_provider_of_a_required_service_is_turned_on_without_asking() -> None:
    specs = {"store": {"provides": {"services": ["store"]}}, "user": {"requires": {"services": ["store"]}}}
    host, _, client = build(specs, ["store", "user"])
    out = client.post("/extensions/user/enable").json()
    assert out["ok"] is True and out["enabled"] == ["store", "user"] and host.is_on("store")


def test_an_always_on_service_provider_needs_nothing() -> None:
    specs = {"svc": {"enabled": "always", "provides": {"services": ["svc"]}}, "user": {"requires": {"services": ["svc"]}}}
    host, _, client = build(specs, ["user"])
    assert client.post("/extensions/user/enable").json()["enabled"] == ["user"]


def test_disable_turns_off_only_the_target() -> None:
    host, _, client = build(CHAIN, [])
    out = client.post("/extensions/c/disable").json()
    assert out["ok"] is True and out["disabled"] == ["c"]
    assert not host.is_on("c") and host.is_on("b") and host.is_on("a")
    assert client.post("/extensions/c/disable").json()["disabled"] == []  # already off


def test_missing_requirements_and_dependents() -> None:
    host, _, _ = build(CHAIN, ["b", "c"])
    assert host.missing_requirements("a") == ["c", "b"]
    assert host.missing_requirements("c") == []
    assert host.dependents("c") == ["b"] and host.dependents("b") == ["a"]
    assert host.dependents("b", enabled_only=True) == ["a"] and host.dependents("c", enabled_only=True) == []


def test_unknown_extension_is_404() -> None:
    _, _, client = build(CHAIN, [])
    assert client.post("/extensions/nope/enable").status_code == 404 and client.post("/extensions/nope/disable").status_code == 404


def test_a_bundled_extension_with_its_google_service_enables_directly() -> None:
    """google is an always-on service: calendar needs nothing else turned on."""
    from personal_os.extensions.host import builtin_manifest
    google, calendar = builtin_manifest("google"), builtin_manifest("calendar")
    assert google.enabled == "always" and "google" in calendar.requires_services and calendar.requires_extensions == ()
