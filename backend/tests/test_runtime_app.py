"""Runtime slots in the running app: a chat turn against selected providers (the record of what served it, the notices), the
settings routes that are the only way to select one, and a profile's default selecting a provider.

Run: python -m pytest backend/tests/test_runtime_app.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="runtime-app-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from grain_sdk.testing import Harness  # noqa: E402
from personal_os import app as appmod  # noqa: E402
from personal_os import router  # noqa: E402
from personal_os.extensions.defaults import Defaults, runtime_profiles  # noqa: E402
from personal_os.extensions.manifest import Manifest  # noqa: E402
from personal_os.runtime import routes as runtime_routes  # noqa: E402

CODE = '''from grain_sdk.testing import FakeModelClient, FakeRouter, FakeToolSource

LOOK = {"name": "look", "description": "Look at a thing", "schema": {"type": "object", "properties": {"what": {"type": "string"}}}}


def activate(api):
    api.provide("model.client", FakeModelClient(text="from the client", id="client"), id="client", label="Client")
    api.provide("model.client", FakeModelClient(fail_at=0, id="broken"), id="broken", label="Broken")
    api.provide("model.router", FakeRouter({"model": "test-model", "effort": "low", "reason": "demo router", "no_think": False}),
                id="router", label="Router")
    api.provide("tools.source", FakeToolSource([LOOK]), id="src", label="Source")
'''
ENTRIES = [{"slot": "model.client", "id": "client", "label": "Client"}, {"slot": "model.client", "id": "broken", "label": "Broken"},
           {"slot": "model.router", "id": "router", "label": "Router"}, {"slot": "tools.source", "id": "src", "label": "Source"}]


@pytest.fixture
def ext(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "extension.json").write_text(json.dumps({
        "id": "demo", "name": "Demo", "version": "1.0.0", "apiVersion": 1, "permissions": ["runtime:model", "runtime:router", "runtime:tools"],
        "contributes": {"runtime": ENTRIES}}), "utf-8")
    (root / "extension.py").write_text(CODE, "utf-8")
    return root


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(router, "_failures", {})
    yield
    Defaults(appmod.db).revert("calm")
    appmod.db.set_settings({"hiddenViews": [v for v in appmod.settings().get("hiddenViews") or () if v != "demo"]})


SETTINGS = {"permissionMode": "allow_all", "autoRoute": True, "defaultModel": "test-model"}


def runtime_span(turn: Any) -> dict[str, Any] | None:
    done = next(d for ev, d in turn.events if ev == "done")
    return next((s for s in done["trace"] if s["kind"] == "runtime"), None)


# ---- a turn ----
def test_a_turn_with_nothing_selected_records_nothing_and_behaves_as_before(ext: Path) -> None:
    with Harness(ext, SETTINGS) as h:
        turn = h.chat("hello")
        assert turn.text == "ok" and "runtime" not in turn.context_used and runtime_span(turn) is None


def test_a_selected_client_serves_the_turn_and_the_reply_says_so(ext: Path) -> None:
    with Harness(ext, SETTINGS) as h:
        h.select("model.client", "demo/client")
        turn = h.chat("hello")
        assert turn.text == "from the client" and turn.error is None
        [used] = [r for r in turn.context_used["runtime"] if r["slot"] == "model.client"]
        assert (used["provider"], used["source"], used["default"], used["reason"]) == (
            "demo/client", "global", False, "Using Client (model client) because your Settings select it.")
        assert runtime_span(turn)["meta"]["providers"] == [used["reason"]]
        assert "turn" not in [e for e, _ in turn.events]  # nothing else changed in the stream


def test_a_client_that_fails_before_replying_is_replaced_by_the_default_with_a_notice(ext: Path) -> None:
    with Harness(ext, SETTINGS) as h:
        h.select("model.client", "demo/broken")
        seen: list[Any] = []
        sub = appmod.host.bus.subscribe("runtime.fallback", seen.append, owner="t", order=0)
        turn = h.chat("hello")
        sub.dispose()
        assert turn.text == "ok" and turn.error is None
        done = next(d for ev, d in turn.events if ev == "done")
        assert "default client answered" in done["notice"]
        assert [(p["slot"], p["wanted"], p["used"]) for p in seen] == [("model.client", "demo/broken", "core/openai-compatible")]


def test_a_selected_router_decides_and_is_recorded(ext: Path) -> None:
    with Harness(ext, SETTINGS) as h:
        h.select("model.router", "demo/router")
        turn = h.chat("hello")
        status = [d for ev, d in turn.events if ev == "status" and d.get("kind") == "route"]
        assert status and status[0]["why"] == "demo router"
        assert [r["provider"] for r in turn.context_used["runtime"]] == ["demo/router"]


def test_a_selection_whose_extension_is_gone_ends_the_turn_when_the_default_is_less_private(ext: Path) -> None:
    with Harness(ext, {**SETTINGS, "baseUrl": "https://api.example.com/v1"}) as h:
        h.select("model.client", "gone/client")
        turn = h.chat("hello")
        assert turn.error is None and turn.text == "" and any(ev == "error" and "nothing was run" in d["message"] for ev, d in turn.events)


def test_a_selected_source_offers_its_tool_and_a_call_runs_through_the_loop(ext: Path) -> None:
    with Harness(ext, SETTINGS) as h:
        assert not any("x__demo__look" in t for t in h.chat("hello").tools)
        h.select("tools.source", {"enable": ["demo/src"]})
        call = {"id": "c1", "name": "x__demo__look", "arguments": {"what": "the sky"}}
        turn = h.chat("look at it", model_script=[{"tool_calls": [call]}, "seen"])
        assert any("x__demo__look" in t for t in turn.tools) and turn.text == "seen"
        source = appmod.host.runtime.provider("tools.source", "demo/src").impl
        assert source.calls == [("look", {"what": "the sky"})]
        assert "look done" in str(turn.requests[1])
        [used] = turn.context_used["runtime"]
        assert used["slot"] == "tools.source" and used["extra"] == ["demo/src"]


def test_a_source_tool_in_manual_mode_asks_and_cannot_be_granted_for_good(ext: Path) -> None:
    with Harness(ext, {**SETTINGS, "permissionMode": "manual"}) as h:
        h.select("tools.source", {"enable": ["demo/src"]})
        assert appmod.tool_sources.tools(appmod.runtime_selections.scopes(None, None))[0].slug == "x__demo__look"
        modes, schemas = appmod._mcp_tooling(None, None)
        assert modes["x__demo__look"] == "ask" and schemas[0]["function"]["description"].startswith("[Source — extension tool source]")
    assert "x__demo__look" not in appmod._mcp_tooling(None, None)[0]


# ---- the routes ----
@pytest.fixture
def api() -> TestClient:
    app = FastAPI()
    app.include_router(runtime_routes.router(appmod.host.runtime, appmod.runtime_selections, appmod.tool_sources))
    return TestClient(app)


def put(api: TestClient, slot: str, value: Any, scope: str = "global", scope_id: str | None = None, confirm: bool = False) -> Any:
    return api.put("/runtime", json={"slot": slot, "scope": scope, "scope_id": scope_id, "value": value, "confirm": confirm})


def test_selections_are_stored_per_scope_and_narrower_ones_win(ext: Path, api: TestClient) -> None:
    with Harness(ext, SETTINGS):
        project = appmod.projects.create("P")["id"]
        chat = appmod.convos.create(project, "C", "test-model")["id"]
        changes: list[Any] = []
        sub = appmod.host.bus.subscribe("runtime.changed", changes.append, owner="t", order=0)
        assert put(api, "model.router", "demo/router").status_code == 200
        assert appmod.db.get_settings()["runtime"] == {"model.router": "demo/router"}
        r = api.get("/runtime/resolve", params={"conversation_id": chat}).json()["model.router"]
        assert (r["provider"], r["source"], r["default"]) == ("demo/router", "global", False)
        put(api, "model.router", "core/heuristic", "project", project)
        assert appmod.projects.get(project)["runtime"] == {"model.router": "core/heuristic"}
        assert api.get("/runtime/resolve", params={"conversation_id": chat}).json()["model.router"]["source"] == "project"
        assert api.get("/runtime/resolve").json()["model.router"]["source"] == "global"  # no chat, no project: global
        put(api, "model.router", "demo/router", "chat", chat)
        assert appmod.convos.get(chat)["settings"]["runtime"] == {"model.router": "demo/router"}
        out = api.get("/runtime", params={"conversation_id": chat}).json()
        slot = next(s for s in out["slots"] if s["id"] == "model.router")
        assert slot["selected"] == {"global": "demo/router", "project": "core/heuristic", "chat": "demo/router"}
        assert slot["resolution"]["source"] == "chat" and {p["id"] for p in slot["providers"]} == {"core/heuristic", "demo/router"}
        put(api, "model.router", None, "chat", chat)  # clearing falls back to the next scope
        assert api.get("/runtime/resolve", params={"conversation_id": chat}).json()["model.router"]["source"] == "project"
        sub.dispose()
        assert [(c["slot"], c["scope"], c["scope_id"], c["new"], c["by"]) for c in changes] == [
            ("model.router", "global", None, "demo/router", "user"), ("model.router", "project", project, "core/heuristic", "user"),
            ("model.router", "chat", chat, "demo/router", "user"), ("model.router", "chat", chat, None, "user")]


def test_a_looser_selection_needs_a_confirmation_that_shows_the_difference(ext: Path, api: TestClient) -> None:
    with Harness(ext, {**SETTINGS, "baseUrl": "http://127.0.0.1:9/v1"}):
        r = put(api, "model.client", "demo/client")  # a remote client over the local built-in one
        assert r.status_code == 409 and r.json()["detail"]["needs_confirm"] is True
        assert "Client is remote and OpenAI-compatible endpoint is local" in r.json()["detail"]["diff"][0]
        assert not appmod.db.get_settings().get("runtime")
        assert put(api, "model.client", "demo/client", confirm=True).status_code == 200
        assert appmod.db.get_settings()["runtime"] == {"model.client": "demo/client"}
        assert put(api, "model.client", "core/openai-compatible").status_code == 200  # a stricter or equal one applies silently
        r = put(api, "tools.source", {"enable": ["demo/src"]})
        assert r.status_code == 409 and r.json()["detail"]["diff"] == ["Source adds its tools"]
        assert put(api, "tools.source", {"enable": ["demo/src"]}, confirm=True).status_code == 200


def test_the_routes_refuse_what_cannot_be_selected(ext: Path, api: TestClient) -> None:
    with Harness(ext, SETTINGS):
        assert put(api, "model.nothing", "x").status_code == 404
        assert put(api, "model.router", "demo/missing").status_code == 422
        assert put(api, "model.router", {"enable": ["demo/router"]}).status_code == 422
        assert put(api, "tools.source", "demo/src").status_code == 422
        assert put(api, "tools.source", {"disable": ["core/builtin"]}).status_code == 422
        assert put(api, "agent.loop", "demo/router").status_code == 422
        assert put(api, "model.router", "demo/router", "project").status_code == 422
        assert put(api, "model.router", "demo/router", "chat", "nope").status_code == 404
        assert put(api, "model.router", "demo/router", "project", "nope").status_code == 404
        appmod.db.set_settings({"hiddenViews": ["demo"]})
        assert put(api, "model.router", "demo/router").status_code == 422
        assert not appmod.db.get_settings().get("runtime")


def test_a_plain_settings_write_cannot_select_a_provider_and_the_agent_has_no_tool_for_it(ext: Path) -> None:
    with Harness(ext, SETTINGS):
        appmod.put_settings({"runtime": {"model.router": "demo/router"}})
        assert not appmod.db.get_settings().get("runtime")
        assert [n for n in appmod.toolbox.specs if "runtime" in n or "provider" in n] == []


def test_accepting_a_partly_enforced_dimension_is_the_users_alone(ext: Path, api: TestClient) -> None:
    with Harness(ext, SETTINGS):
        r = api.put("/runtime/accept-partial", json={"provider": "core/os-default", "dimensions": ["network"]})
        assert r.json() == {"core/os-default": ["network"]}
        assert appmod.db.get_settings()["runtime"]["accept_partial"] == {"core/os-default": ["network"]}
        assert api.put("/runtime/accept-partial", json={"provider": "core/os-default", "dimensions": ["luck"]}).status_code == 422
        assert api.put("/runtime/accept-partial", json={"provider": "demo/router", "dimensions": ["network"]}).status_code == 422
        assert api.put("/runtime/accept-partial", json={"provider": "core/os-default", "dimensions": []}).json() == {}
        put(api, "model.router", "demo/router")
        assert "accept_partial" in appmod.db.get_settings()["runtime"] and appmod.db.get_settings()["runtime"]["model.router"] == "demo/router"


def test_the_test_button_runs_the_contract_and_a_tool_can_be_accepted_back(ext: Path, api: TestClient) -> None:
    with Harness(ext, SETTINGS):
        assert api.post("/runtime/test", json={"slot": "model.router", "provider": "demo/router"}).json()["ok"] is True
        assert api.post("/runtime/test", json={"slot": "model.router", "provider": "demo/nope"}).json()["ok"] is False
        assert api.post("/runtime/test", json={"slot": "model.nothing", "provider": "x"}).status_code == 404
        assert api.post("/runtime/tools/x__demo__ghost/accept").status_code == 404
        assert api.get("/runtime").json()["withheld_tools"] == []


# ---- a profile's default ----
PROFILE = {"id": "calm", "name": "Calm", "version": "1.0.0", "apiVersion": 1, "kind": "profile"}


def profile(**runtime: Any) -> Manifest:
    return Manifest.from_dict({**PROFILE, "contributes": {"defaults": {"runtime": runtime}}})


def test_a_profile_default_selects_a_provider_and_shows_as_the_profile_layer(ext: Path) -> None:
    with Harness(ext, {**SETTINGS, "baseUrl": "http://127.0.0.1:9/v1"}):
        defaults = Defaults(appmod.db)
        changes: list[Any] = []
        sub = appmod.host.bus.subscribe("runtime.changed", changes.append, owner="t", order=0)
        assert defaults.apply(profile(**{"model.router": "demo/router"})) == []
        assert runtime_profiles(appmod.db.get_settings()) == (("calm", {"model.router": "demo/router"}),)
        res = appmod.host.runtime.resolve("model.router", appmod.runtime_selections.scopes(None, None))
        assert (res.provider, res.source) == ("demo/router", "profile:calm")
        assert [(c["slot"], c["scope"], c["scope_id"], c["new"], c["by"]) for c in changes] == [("model.router", "profile", "calm", "demo/router", "profile:calm")]
        sub.dispose()
        defaults.revert("calm")
        assert runtime_profiles(appmod.db.get_settings()) == ()
        assert appmod.host.runtime.resolve("model.router", appmod.runtime_selections.scopes(None, None)).default


def test_a_profile_default_that_is_looser_is_held_until_the_user_clicks(ext: Path) -> None:
    with Harness(ext, {**SETTINGS, "baseUrl": "http://127.0.0.1:9/v1"}):
        defaults = Defaults(appmod.db)
        wanted = profile(**{"model.client": "demo/client"})
        [change] = defaults.plan(wanted)
        assert change.looser and "Client is remote" in change.diff[0] and change.to_dict()["diff"] == list(change.diff)
        [held] = defaults.apply(wanted)
        assert held.key == "runtime:model.client" and runtime_profiles(appmod.db.get_settings()) == ()
        defaults.revert("calm")
        assert defaults.apply(wanted, confirmed=True) == []
        assert appmod.host.runtime.resolve("model.client", appmod.runtime_selections.scopes(None, None)).provider == "demo/client"
        defaults.revert("calm")
        stricter = profile(**{"model.client": "core/openai-compatible"})
        assert not any(c.looser for c in defaults.plan(stricter))  # choosing the built-in loosens nothing: no click


def test_the_profile_layer_sits_between_the_project_and_the_global_selection(ext: Path) -> None:
    with Harness(ext, SETTINGS):
        appmod.db.set_settings({"runtime": {"model.router": "core/heuristic"}})
        Defaults(appmod.db).apply(profile(**{"model.router": "demo/router"}))
        assert appmod.host.runtime.resolve("model.router", appmod.runtime_selections.scopes(None, None)).source == "profile:calm"
        project = appmod.projects.create("Q")["id"]
        appmod.projects.update(project, {"runtime": {"model.router": "core/heuristic"}})
        assert appmod.host.runtime.resolve("model.router", appmod.runtime_selections.scopes(None, project)).source == "project"
        Defaults(appmod.db).revert("calm")
