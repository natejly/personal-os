"""Runtime slots: the registry, selection order and its visible source, provider registration rules, fallbacks, the
manifest's contributes.runtime and the contract suites.

Run: python -m pytest backend/tests/test_runtime_slots.py
"""
from __future__ import annotations

import asyncio
import itertools
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from grain_sdk.testing import FakeModelClient, FakeRouter, FakeSandbox, FakeToolSource, slot_contract  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.extensions.host import ExtensionContext, ExtensionHost  # noqa: E402
from personal_os.extensions.manifest import Manifest, ManifestError  # noqa: E402
from personal_os.runtime.slots import SLOTS, Scopes  # noqa: E402

RUNNER = '''def activate(api):\n    pass\n'''


def context(hidden: list[str] | None = None) -> ExtensionContext:
    return ExtensionContext(db=Database(Path(tempfile.mkdtemp(prefix="runtime-db-"))), hidden_views=lambda: hidden if hidden is not None else (),
                            services={}, settings=lambda: {}, set_settings=lambda v: None, sid=lambda p: p, wsid=lambda p: p,
                            publish=lambda _n, _d: None)


def folder(tmp_path: Path, ext_id: str, entries: list[dict[str, Any]], permissions: list[str] | None = None) -> Path:
    root = tmp_path / ext_id
    root.mkdir()
    (root / "extension.json").write_text(json.dumps({
        "id": ext_id, "name": ext_id.title(), "version": "1.0.0", "apiVersion": 1, "contributes": {"runtime": entries},
        "permissions": permissions if permissions is not None else sorted({SLOTS[e["slot"]].permission for e in entries})}), "utf-8")
    (root / "extension.py").write_text(RUNNER, "utf-8")
    return root


def entry(slot: str, id_: str, label: str | None = None) -> dict[str, Any]:
    return {"slot": slot, "id": id_, "label": label or id_.title()}


@pytest.fixture
def host() -> ExtensionHost:
    return ExtensionHost()


# ---- selection order and the visible source ----
LAYERS = ("chat", "project", "profile:lean", "global")


def scopes_with(**chosen: str) -> Scopes:
    """Scopes where each named layer selects the model.router provider it is given (`profile` stands for the profile layer)."""
    sel = {k: {"model.router": v} for k, v in chosen.items()}
    return Scopes(chat=sel.get("chat", {}), project=sel.get("project", {}), profiles=(("lean", sel["profile"]),) if "profile" in sel else (),
                  global_=sel.get("global", {}))


@pytest.fixture
def routers(host: ExtensionHost, tmp_path: Path) -> ExtensionHost:
    scope = host.load_folder(folder(tmp_path, "acme", [entry("model.router", "one", "One"), entry("model.router", "two", "Two")]), context())
    scope.provide("model.router", FakeRouter(id="one"), id="one", label="One")
    scope.provide("model.router", FakeRouter(id="two"), id="two", label="Two")
    return host


def test_with_nothing_selected_every_slot_is_its_default(routers: ExtensionHost) -> None:
    for slot, res in routers.runtime.resolve_all(Scopes()).items():
        assert (res.provider, res.source, res.default) == (SLOTS[slot].default_id, "default", True)
        assert not res.fell_back_from and "by default" in res.reason


@pytest.mark.parametrize("high,low", [(a, b) for a, b in itertools.combinations(("chat", "project", "profile", "global"), 2)])
def test_a_narrower_scope_beats_a_wider_one(routers: ExtensionHost, high: str, low: str) -> None:
    res = routers.runtime.resolve("model.router", scopes_with(**{high: "acme/one", low: "acme/two"}))
    source = "profile:lean" if high == "profile" else high
    assert (res.provider, res.source, res.default) == ("acme/one", source, False)
    phrase = {"chat": "this chat selects it", "project": "this project selects it", "profile:lean": "the profile 'lean' selects it",
              "global": "your Settings select it"}[source]
    assert res.reason == f"Using One (model router) because {phrase}."


def test_a_scope_can_select_the_default_over_a_wider_choice(routers: ExtensionHost) -> None:
    res = routers.runtime.resolve("model.router", Scopes(chat={"model.router": "core/heuristic"}, global_={"model.router": "acme/one"}))
    assert res.provider == "core/heuristic" and res.default


def test_tool_sources_merge_from_global_up_to_chat(host: ExtensionHost, tmp_path: Path) -> None:
    scope = host.load_folder(folder(tmp_path, "acme", [entry("tools.source", "a"), entry("tools.source", "b")]), context())
    for i in ("a", "b"):
        scope.provide("tools.source", FakeToolSource([]), id=i, label=i.upper())
    rt = host.runtime
    both = {"tools.source": {"enable": ["acme/a", "acme/b"]}}
    res = rt.resolve("tools.source", Scopes(global_=both))
    assert (res.provider, res.extra, res.source, res.default) == ("core/builtin", ("acme/a", "acme/b"), "global", False)
    narrowed = rt.resolve("tools.source", Scopes(global_=both, project={"tools.source": {"disable": ["acme/a"]}}))
    assert narrowed.extra == ("acme/b",) and narrowed.source == "project"
    again = rt.resolve("tools.source", Scopes(global_=both, project={"tools.source": {"disable": ["acme/a"]}}, chat={"tools.source": {"enable": ["acme/a"]}}))
    assert again.extra == ("acme/b", "acme/a") and again.source == "chat"
    assert rt.resolve("tools.source", Scopes()).default


# ---- registering a provider ----
def test_a_provider_needs_the_slots_permission(host: ExtensionHost, tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="needs the permission 'runtime:router'"):
        host.load_folder(folder(tmp_path, "acme", [entry("model.router", "x")], permissions=[]), context())


def test_the_api_checks_slot_manifest_entry_label_and_interface(host: ExtensionHost, tmp_path: Path) -> None:
    scope = host.load_folder(folder(tmp_path, "acme", [entry("model.router", "x", "Ex")]), context())
    with pytest.raises(ValueError, match="not a runtime slot"):
        scope.provide("model.nothing", FakeRouter(), id="x", label="Ex")
    with pytest.raises(ValueError, match="did not declare provider 'y'"):
        scope.provide("model.router", FakeRouter(), id="y", label="Ex")
    with pytest.raises(ValueError, match="labelled 'Wrong'"):
        scope.provide("model.router", FakeRouter(), id="x", label="Wrong")
    with pytest.raises(ValueError, match="does not implement"):
        scope.provide("model.router", object(), id="x", label="Ex")
    scope.provide("model.router", FakeRouter(), id="x", label="Ex")


def test_duplicate_ids_in_one_manifest_are_a_validation_error() -> None:
    with pytest.raises(ManifestError, match="twice"):
        Manifest.from_dict({"id": "a", "name": "A", "version": "1.0.0", "apiVersion": 1, "permissions": ["runtime:router"],
                            "contributes": {"runtime": [entry("model.router", "x"), entry("model.router", "x")]}})


def test_manifest_runtime_entries_are_validated() -> None:
    base = {"id": "a", "name": "A", "version": "1.0.0", "apiVersion": 1, "permissions": ["runtime:router"]}
    for bad in ([{"slot": "model.nope", "id": "x", "label": "X"}], [{"slot": "model.router", "id": "X Y", "label": "X"}],
                [{"slot": "model.router", "id": "x"}], [{"slot": "model.router", "id": "x", "label": "X", "color": 1}], "x"):
        with pytest.raises(ManifestError):
            Manifest.from_dict({**base, "contributes": {"runtime": bad}})
    with pytest.raises(ManifestError, match="no dot"):
        Manifest.from_dict({**base, "provides": {"services": ["sandbox.backend"]}})


def test_a_profile_cannot_contribute_a_runtime_provider() -> None:
    with pytest.raises(ManifestError):
        Manifest.from_dict({"id": "p", "name": "P", "version": "1.0.0", "apiVersion": 1, "kind": "profile",
                            "contributes": {"runtime": [entry("model.router", "x")]}})
    Manifest.from_dict({"id": "p", "name": "P", "version": "1.0.0", "apiVersion": 1, "kind": "profile",
                        "contributes": {"defaults": {"runtime": {"model.router": "acme/x"}}}})


def test_a_user_extension_may_not_provide_the_sandbox_or_the_loop(host: ExtensionHost, tmp_path: Path) -> None:
    for slot in ("sandbox.backend", "agent.loop"):
        with pytest.raises((PermissionError, ValueError)):
            host.load_folder(folder(tmp_path, f"x{slot[0]}", [entry(slot, "y")]), context())
    assert host.extensions == {} and [p.id for p in host.runtime.providers("sandbox.backend")] == ["core/os-default"]


def test_a_built_in_extension_may_provide_the_sandbox(host: ExtensionHost, tmp_path: Path) -> None:
    root = folder(tmp_path, "boxes", [entry("sandbox.backend", "box")])
    from personal_os.extensions.manifest import load_manifest
    scope = host._add(load_manifest(root / "extension.json"), root=root, ctx=context())
    scope.provide("sandbox.backend", FakeSandbox(id="box"), id="box", label="Box")
    assert host.runtime.provider("sandbox.backend", "boxes/box").tier == "built-in"


def test_the_loop_slot_takes_no_other_provider_yet(host: ExtensionHost) -> None:
    from personal_os.runtime.slots import Provider
    with pytest.raises(ValueError, match="agent.loop"):
        host.runtime.declare(Provider("a/loop", "agent.loop", "Loop", extension="a"))


def test_a_model_clients_kinds_are_checked(host: ExtensionHost, tmp_path: Path) -> None:
    scope = host.load_folder(folder(tmp_path, "acme", [entry("model.client", "c")]), context())
    client = FakeModelClient()
    client.kinds = frozenset({"telepathy"})
    with pytest.raises(ValueError, match="kinds"):
        scope.provide("model.client", client, id="c", label="C")


# ---- a provider that disappears ----
def test_a_disabled_provider_falls_back_with_one_announcement_and_returns_when_enabled(tmp_path: Path) -> None:
    hidden: list[str] = []
    host = ExtensionHost()
    scope = host.load_folder(folder(tmp_path, "acme", [entry("model.router", "x")]), context(hidden))
    scope.provide("model.router", FakeRouter(), id="x", label="X")
    seen: list[Any] = []
    host.bus.subscribe("runtime.fallback", seen.append, owner="t", order=0)
    sel = Scopes(global_={"model.router": "acme/x"}, conversation_id="c1")
    assert host.runtime.resolve("model.router", sel).provider == "acme/x"
    hidden.append("acme")
    for _ in range(3):
        res = host.runtime.resolve("model.router", sel)
    assert (res.provider, res.fell_back_from, res.refused) == ("core/heuristic", "acme/x", False)
    assert "not available; using the default" in res.reason and not res.default
    assert [(p["slot"], p["wanted"], p["used"], p["refused"], p["conversation_id"]) for p in seen] == [("model.router", "acme/x", "core/heuristic", False, "c1")]
    hidden.clear()
    assert host.runtime.resolve("model.router", sel).provider == "acme/x"
    hidden.append("acme")
    host.runtime.resolve("model.router", sel)
    assert len(seen) == 2  # a new change is announced again


def test_an_unknown_selection_for_a_slot_with_a_safety_dimension_is_refused(host: ExtensionHost) -> None:
    res = host.runtime.resolve("sandbox.backend", Scopes(global_={"sandbox.backend": "gone/box"}))
    assert res.refused and res.provider == "gone/box" and "nothing was run" in res.reason
    client = host.runtime.resolve("model.client", Scopes(global_={"model.client": "gone/client"}))
    assert client.refused


def test_a_disabled_local_client_does_not_fall_back_to_a_remote_default(tmp_path: Path) -> None:
    hidden: list[str] = []
    host = ExtensionHost()
    scope = host.load_folder(folder(tmp_path, "acme", [entry("model.client", "c")]), context(hidden))
    scope.provide("model.client", FakeModelClient(locality="local"), id="c", label="C")
    sel = Scopes(global_={"model.client": "acme/c"})
    assert host.runtime.resolve("model.client", sel).provider == "acme/c" and host.runtime.traits("model.client", "acme/c")[0] == "local"
    hidden.append("acme")
    assert host.runtime.resolve("model.client", sel).refused  # the built-in client's locality is remote with no provider configured


def test_a_disabled_remote_client_falls_back_to_the_default(tmp_path: Path) -> None:
    hidden: list[str] = []
    host = ExtensionHost()
    scope = host.load_folder(folder(tmp_path, "acme", [entry("model.client", "c")]), context(hidden))
    scope.provide("model.client", FakeModelClient(locality="remote"), id="c", label="C")
    sel = Scopes(global_={"model.client": "acme/c"})
    host.runtime.resolve("model.client", sel)
    hidden.append("acme")
    res = host.runtime.resolve("model.client", sel)
    assert not res.refused and res.provider == "core/openai-compatible" and res.fell_back_from == "acme/c"


def test_the_alternative_is_none_without_providers_and_for_the_default(host: ExtensionHost, tmp_path: Path) -> None:
    assert host.runtime.alternative("model.router") is None and not host.runtime.has_alternatives("model.router")
    scope = host.load_folder(folder(tmp_path, "acme", [entry("model.router", "x")]), context())
    scope.provide("model.router", FakeRouter(), id="x", label="X")
    host.runtime.scopes = lambda: Scopes(global_={"model.router": "acme/x"})
    assert host.runtime.alternative("model.router").id == "acme/x"
    host.runtime.scopes = Scopes
    assert host.runtime.alternative("model.router") is None


# ---- loosening ----
def test_choosing_a_remote_client_over_a_local_one_loosens_and_the_reverse_does_not(tmp_path: Path) -> None:
    host = ExtensionHost()
    scope = host.load_folder(folder(tmp_path, "acme", [entry("model.client", "far"), entry("model.client", "near")]), context())
    scope.provide("model.client", FakeModelClient(locality="remote"), id="far", label="Far")
    scope.provide("model.client", FakeModelClient(locality="local"), id="near", label="Near")
    rt = host.runtime
    local_now = rt.resolve("model.client", Scopes(global_={"model.client": "acme/near"}))
    assert rt.loosening("model.client", local_now, "acme/far") == ["Far is remote and Near is local: your prompts would leave this computer"]
    remote_now = rt.resolve("model.client", Scopes(global_={"model.client": "acme/far"}))
    assert rt.loosening("model.client", remote_now, "acme/near") == []


def test_a_tool_source_always_loosens_and_a_router_never_does(host: ExtensionHost, tmp_path: Path) -> None:
    scope = host.load_folder(folder(tmp_path, "acme", [entry("tools.source", "s"), entry("model.router", "r")]), context())
    scope.provide("tools.source", FakeToolSource([]), id="s", label="S")
    scope.provide("model.router", FakeRouter(), id="r", label="R")
    now = host.runtime.resolve("tools.source", Scopes())
    assert host.runtime.loosening("tools.source", now, {"enable": ["acme/s"]}) == ["S adds its tools"]
    assert host.runtime.loosening("tools.source", now, {"disable": ["acme/s"]}) == []
    assert host.runtime.loosening("model.router", host.runtime.resolve("model.router", Scopes()), "acme/r") == []


# ---- the contract suites ----
def test_the_default_router_and_client_shapes_pass_their_contracts() -> None:
    from personal_os.router import Heuristic
    slot_contract("model.router", Heuristic)
    slot_contract("agent.loop", lambda: type("Loop", (), {"id": "x"})())


def test_a_client_that_needs_repair_fails_its_contract() -> None:
    slot_contract("model.client", FakeModelClient)
    for script in ([{"type": "delta", "text": "x"}], [{"type": "end", "tool_calls": [], "finish_reason": "stop"}] * 2,
                   [{"type": "bogus"}, {"type": "end", "tool_calls": [], "finish_reason": "stop"}],
                   [{"type": "end", "tool_calls": [{"name": "x"}], "finish_reason": "stop"}]):
        with pytest.raises(AssertionError):
            slot_contract("model.client", lambda s=script: FakeModelClient(script=s))
    with pytest.raises(AssertionError, match="locality"):
        slot_contract("model.client", lambda: FakeModelClient(locality="nearby"))


def test_a_router_that_is_slow_or_returns_the_wrong_shape_fails_its_contract() -> None:
    slot_contract("model.router", FakeRouter)
    with pytest.raises(AssertionError, match="budget"):
        slot_contract("model.router", lambda: FakeRouter(delay_s=0.2))
    with pytest.raises(AssertionError, match="route"):
        slot_contract("model.router", lambda: FakeRouter({"model": "m"}))


def test_a_tool_source_with_a_low_danger_or_a_bad_name_fails_its_contract() -> None:
    good = {"name": "look", "description": "d", "schema": {"type": "object"}}
    slot_contract("tools.source", lambda: FakeToolSource([good]))
    with pytest.raises(AssertionError, match="external"):
        slot_contract("tools.source", lambda: FakeToolSource([{**good, "danger": "safe"}]))
    with pytest.raises(AssertionError, match="bad tool name"):
        slot_contract("tools.source", lambda: FakeToolSource([{**good, "name": "has space"}]))
    with pytest.raises(AssertionError, match="unique"):
        slot_contract("tools.source", lambda: FakeToolSource([good, good]))


def test_a_sandbox_that_claims_confinement_it_does_not_give_fails_its_contract() -> None:
    full = dict.fromkeys(("fs_write", "fs_read_deny", "network", "process"), "full")
    slot_contract("sandbox.backend", lambda: FakeSandbox({}))  # lists nothing: wrap must raise, and it does
    slot_contract("sandbox.backend", lambda: FakeSandbox({"workspace-write": {**full, "fs_write": "partial"}}))  # partial is never probed
    with pytest.raises(AssertionError, match="outside the allowed roots succeeded"):
        slot_contract("sandbox.backend", lambda: FakeSandbox({"workspace-write": full}))  # a liar: wrap leaves the argv alone


def test_a_sandbox_that_lists_a_mode_but_wraps_it_anyway_fails_when_it_does_not_list_it() -> None:
    class Lax(FakeSandbox):
        def wrap(self, req: Any, argv: list[str]) -> list[str]:
            return argv
    with pytest.raises(AssertionError, match="must raise"):
        slot_contract("sandbox.backend", lambda: Lax({}))


def test_the_contract_runs_from_the_registry(host: ExtensionHost, tmp_path: Path) -> None:
    scope = host.load_folder(folder(tmp_path, "acme", [entry("model.router", "ok"), entry("model.router", "slow")]), context())
    scope.provide("model.router", FakeRouter(), id="ok", label="Ok")
    scope.provide("model.router", FakeRouter(delay_s=0.2), id="slow", label="Slow")
    assert asyncio.run(host.runtime.test("model.router", "acme/ok"))["ok"] is True
    bad = asyncio.run(host.runtime.test("model.router", "acme/slow"))
    assert bad["ok"] is False and "budget" in bad["detail"]
    assert asyncio.run(host.runtime.test("model.router", "acme/missing"))["ok"] is False
