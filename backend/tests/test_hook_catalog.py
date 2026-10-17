"""The hook catalog (design doc 10.9): the registry against every call site, the strict bus, the subscriber rules, the
append-only golden file and the generated outputs."""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import shutil
import sys
import tempfile
import typing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="hook-catalog-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import grain_sdk  # noqa: E402
from personal_os.extensions import catalog, hookgen, hooks as hooks_mod  # noqa: E402
from personal_os.extensions.authoring import cli, hookscan  # noqa: E402
from personal_os.extensions.catalog import CATALOG_VERSION, HOOKS  # noqa: E402
from personal_os.extensions.hookschema import problem, schema_of  # noqa: E402
from personal_os.extensions.hooks import Ask, Cancel, Deny, HookBus, HookContractError  # noqa: E402
from personal_os.extensions.host import ExtensionHost  # noqa: E402
from personal_os.extensions.manifest import Manifest, ManifestError  # noqa: E402

CHAT = {"id": "c1", "project_id": None}
SELECT = {"model": "m", "effort": "high", "reason": "", "tier": "default"}


def run(coro: Any) -> Any:
    import asyncio
    return asyncio.run(coro)


# ---- the scan ----
def test_every_call_site_matches_the_catalog() -> None:
    sites = hookscan.scan()
    assert len(sites) >= len(HOOKS)
    assert hookscan.site_problems(sites) == []


def test_every_cataloged_hook_is_fired_somewhere() -> None:
    assert hookscan.dead_hooks(hookscan.scan()) == []


def test_a_hook_with_no_call_site_is_reported() -> None:
    sites = [s for s in hookscan.scan() if s.hook != "job.done"]
    assert hookscan.dead_hooks(sites) == ["job.done: in the catalog but fired nowhere"]


BAD_SITES = '''\
HOOK = "chat.created"
OTHER = "project.created"


class Thing:
    def fire(self, hook, payload):
        self.bus.fire(hook, payload)  # forwards its own parameter: its callers are scanned

    def good(self, bus, host, extra):
        name = extra.name
        host.fire("chat.created", {"id": "c", "project_id": None})
        host.fire(HOOK, {"id": "c", "project_id": None})
        bus.fire(name, {"id": "p"})  # hook: project.created
        host.fire(OTHER, {**extra})
        bus.emit_sync("model.request", {"model": "m", "message_count": 1, "tool_names": [], "tokens_est": 1})
        bus.fire("project.deleted",
                 {"id": "p"})

    def bad(self, bus, host, extra):
        name = extra.name
        host.fire("no.such.hook", {})
        bus.emit("tool.pre", {})
        host.fire("chat.created", {"id": "c", "project_id": None, "oops": 1})
        host.fire("chat.created", {"id": "c"})
        bus.fire(name, {})
        bus.emit("model.request", {"model": "m", "message_count": 1, "tool_names": [], "tokens_est": 1})
'''


def test_a_bad_site_fails_with_its_file_and_line(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text(BAD_SITES, "utf-8")
    first = BAD_SITES.splitlines().index("    def bad(self, bus, host, extra):") + 3
    problems = hookscan.site_problems(hookscan.scan(tmp_path))
    assert [p.split(": ", 1)[0] for p in problems] == [f"bad.py:{first + n}" for n in range(6)]
    unknown, wrong_kind, extra_key, missing_key, no_comment, wrong_sync = problems
    assert "'no.such.hook', which is not in the catalog" in unknown
    assert "tool.pre is a gate hook, called with emit" in wrong_kind
    assert "key 'oops'" in extra_key
    assert "required key 'project_id'" in missing_key
    assert "# hook: <id>" in no_comment
    assert "model.request is a sync hook, called with emit" in wrong_sync


# ---- the registry ----
def test_a_spec_is_consistent() -> None:
    assert hooks_mod.CALL_KIND["fire"] == "emit"
    for spec in HOOKS.values():
        assert spec.since <= CATALOG_VERSION
        assert (spec.subscribers == "grant") == bool(spec.permission), spec.id
        assert (spec.returns is None) == (spec.kind == "emit"), spec.id
        assert spec.section and spec.fires_at and spec.order_failure
        schema_of(spec.payload)  # a payload may only use the types the generated files can express


def test_the_schema_converter_rejects_a_type_it_cannot_express() -> None:
    class Odd(typing.TypedDict):
        when: set[str]
    with pytest.raises(TypeError):
        schema_of(Odd)
    assert schema_of(catalog.ChatPayload) == {
        "type": "object", "properties": {"id": {"type": "string"}, "project_id": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        "required": ["id", "project_id"], "additionalProperties": False}


# ---- the strict bus ----
def test_strict_mode_is_on_in_the_suite() -> None:
    assert hooks_mod.STRICT


def test_a_payload_that_does_not_match_raises_under_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = HookBus()
    with pytest.raises(HookContractError, match="payload of chat.created: 'project_id' is a required property"):
        run(bus.emit("chat.created", {"id": "c"}))
    with pytest.raises(HookContractError, match="payload of chat.created"):
        bus.fire("chat.created", {"id": 1, "project_id": None})
    with pytest.raises(HookContractError, match="payload of model.select"):
        run(bus.pipe("model.select", "x"))
    run(bus.emit("chat.created", CHAT))
    monkeypatch.setattr(hooks_mod, "STRICT", False)
    run(bus.emit("chat.created", {"id": "c"}))  # outside strict mode a payload is not checked


def test_a_call_of_the_wrong_kind_raises_under_strict_and_is_logged_once_otherwise(monkeypatch: pytest.MonkeyPatch,
                                                                                    caplog: pytest.LogCaptureFixture) -> None:
    bus = HookBus()
    with pytest.raises(HookContractError, match="tool.pre is a gate hook, called with emit"):
        run(bus.emit("tool.pre", {}))
    with pytest.raises(HookContractError, match="chat.created is a emit hook, called with gate"):
        run(bus.gate("chat.created", CHAT))
    with pytest.raises(HookContractError, match="tool.pre is a gate hook, called with pipe_sync"):
        bus.pipe_sync("tool.pre", {})
    monkeypatch.setattr(hooks_mod, "STRICT", False)
    with caplog.at_level(logging.ERROR, "personal_os.extensions.hooks"):
        run(bus.emit("tool.pre", {}))
        run(bus.emit("tool.pre", {}))
    assert [r.getMessage() for r in caplog.records] == ["tool.pre is a gate hook, called with emit"]


def test_a_handler_return_that_does_not_match_raises_under_strict() -> None:
    bus = HookBus()
    bus.subscribe("tool.pre", lambda p: "allow", owner="x", order=0)
    pre = {"name": "t", "args": {}, "call_id": "k", "ctx": dict.fromkeys(("run_id", "conversation_id", "project_id", "agent_id", "desk_id")) | {"tainted": False}}
    with pytest.raises(HookContractError, match="return of a tool.pre handler: str is not a Deny"):
        run(bus.gate("tool.pre", pre))
    ok = HookBus()
    ok.subscribe("tool.pre", lambda p: Ask("check"), owner="x", order=0)
    ok.subscribe("model.select", lambda p: {**p, "effort": "low"}, owner="x", order=0)
    ok.subscribe("model.select", lambda p: None, owner="x", order=1)
    assert run(ok.gate("tool.pre", pre)) == Ask("check")
    assert run(ok.pipe("model.select", SELECT))["effort"] == "low"
    bad = HookBus()
    bad.subscribe("model.select", lambda p: {"model": "m"}, owner="x", order=0)
    with pytest.raises(HookContractError, match="return of a model.select handler"):
        run(bad.pipe("model.select", SELECT))
    sync = HookBus()
    sync.subscribe("memory.before_write", lambda p: 3, owner="x", order=0)
    with pytest.raises(HookContractError, match="return of a memory.before_write handler"):
        sync.pipe_sync("memory.before_write", {"content": "c", "kind": "fact", "source": "user", "project_id": None})
    cancel = HookBus()
    cancel.subscribe("channel.outbound", lambda p: Cancel(), owner="x", order=0)
    assert isinstance(run(cancel.pipe("channel.outbound", {"channel": "c", "text": "t", "files": []})), Cancel)


def test_an_object_payload_is_checked_through_its_adapter() -> None:
    run_like = SimpleNamespace(run_id="r", conversation_id="c", kind="chat", desk_id=None, turn=0, status="done", error=None,
                               message_id=None, input={}, cost=0.0, rounds=1, unrelated=object())
    run(HookBus().emit("run.after", run_like))
    with pytest.raises(HookContractError, match="payload of run.after"):
        run(HookBus().emit("run.after", SimpleNamespace(**{**vars(run_like), "status": 3})))
    assert set(HOOKS["run.after"].payload_of(run_like)) == set(catalog.RunAfterPayload.__annotations__)


def test_optional_payload_keys_may_be_left_out() -> None:
    usage = {"model": "m", "kind": "chat", "duration_ms": 1, "estimated": False, "prompt_tokens": 1, "completion_tokens": 1,
             "cached_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0}
    HookBus().emit_sync("model.response", usage)
    HookBus().emit_sync("model.response", {**usage, "conversation_id": "c", "round": 2})
    with pytest.raises(HookContractError, match="model.response"):
        HookBus().emit_sync("model.response", {**usage, "round": "2"})


def test_problem_describes_a_class_or_a_union() -> None:
    assert problem(Deny | Ask, Deny("x")) is None
    assert problem(Deny | Ask, 3) == "int is not a Deny; int is not a Ask"
    assert problem(catalog.ChatPayload | Deny, Deny("x")) is None


# ---- who may subscribe ----
def manifest(**over: Any) -> dict[str, Any]:
    return {"id": "demo", "name": "Demo", "version": "1.0.0", "apiVersion": 1, **over}


def test_a_manifest_must_declare_the_permissions_of_the_hooks_it_subscribes_to() -> None:
    for hook, spec in HOOKS.items():
        raw = manifest(hooks={hook: {}})
        if spec.subscribers == "any":
            Manifest.from_dict(raw)
            continue
        with pytest.raises(ManifestError, match="needs the permission"):
            Manifest.from_dict(raw)
        Manifest.from_dict({**raw, "permissions": list(spec.permission)})


def test_a_hook_for_built_in_extensions_only_is_refused_to_a_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(HOOKS, "tool.taint", dataclasses.replace(HOOKS["tool.taint"], subscribers="builtin"))
    (tmp_path / "extension.json").write_text(json.dumps(manifest(hooks={"tool.taint": {}})), "utf-8")
    (tmp_path / "extension.py").write_text("def activate(api): pass\n", "utf-8")
    with pytest.raises(ValueError, match="only a built-in extension may subscribe to tool.taint"):
        ExtensionHost().load_folder(tmp_path, None)  # type: ignore[arg-type]


# ---- the golden file ----
def test_no_hook_id_has_been_removed_or_retyped() -> None:
    assert hookscan.golden_problems() == []


def test_the_golden_file_catches_a_removed_a_retyped_and_an_unrecorded_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    golden = tmp_path / "golden.json"
    recorded = {"version": CATALOG_VERSION, "hooks": [["gone.away", "emit", "1.0"], ["chat.created", "gate", "1.0"],
                                                     *[[h, s.kind, s.since] for h, s in HOOKS.items() if h not in ("chat.created", "chat.deleted")]]}
    golden.write_text(json.dumps(recorded), "utf-8")
    assert hookscan.golden_problems(golden) == [
        "gone.away: removed from the catalog; retire a hook with `deprecated` instead",
        "chat.created: kind changed from gate to emit without `deprecated` and a CATALOG_VERSION bump",
        'chat.deleted: not in golden.json; add ["chat.deleted", "emit", "1.0"]']
    monkeypatch.setitem(HOOKS, "chat.created", dataclasses.replace(HOOKS["chat.created"], deprecated="use chat.opened"))
    monkeypatch.setattr(hookscan, "CATALOG_VERSION", "2.0")
    assert "chat.created: kind changed from gate to emit" not in " ".join(hookscan.golden_problems(golden))


# ---- the generated files ----
def test_the_generated_files_are_current() -> None:
    assert hookgen.stale() == []


def test_the_sdk_and_ts_types_name_exactly_the_cataloged_hooks() -> None:
    assert set(typing.get_args(grain_sdk.hooks.HookId)) == set(HOOKS)
    ts = hookgen.TS_OUT.read_text("utf-8")
    assert all(f"  '{h}': {s.payload.__name__}\n" in ts and f"  '{h}': '{s.kind}'\n" in ts for h, s in HOOKS.items())
    doc = hookgen.RESEARCH.read_text("utf-8")
    assert all(f"| `{h}` |" in doc for h in HOOKS) and all(f"| `{h}` |" in hookgen.FEATURE_DOC.read_text("utf-8") for h in HOOKS)


@pytest.fixture
def copies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    out = {}
    for name in ("RESEARCH", "FEATURE_DOC", "PY_OUT", "TS_OUT"):
        src: Path = getattr(hookgen, name)
        out[name] = tmp_path / src.name
        shutil.copy(src, out[name])
        monkeypatch.setattr(hookgen, name, out[name])
    return out


def test_gen_hooks_check_fails_on_a_stale_file_and_zero_on_a_clean_tree(copies: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["ext", "gen-hooks", "--check"]) == 0
    for name, edit in (("TS_OUT", lambda t: t.replace("project_id", "projectId", 1)), ("PY_OUT", lambda t: t + "# hand edit\n"),
                       ("FEATURE_DOC", lambda t: t.replace("`app.start`", "`app.begin`")), ("RESEARCH", lambda t: t.replace("the run task's done callback", "the run task is done"))):
        path = copies[name]
        before = path.read_text("utf-8")
        path.write_text(edit(before), "utf-8")
        capsys.readouterr()
        assert cli.main(["ext", "gen-hooks", "--check"]) == 1
        assert f"{path.name} is out of date: run `grain ext gen-hooks`" in capsys.readouterr().out
        assert cli.main(["ext", "check", "--catalog"]) == 1
        assert cli.main(["ext", "gen-hooks"]) == 0
        assert path.read_text("utf-8") == before
    assert cli.main(["ext", "check", "--catalog"]) == 0


def test_a_hook_whose_section_has_no_marked_table_is_reported(copies: dict[str, Path]) -> None:
    copies["RESEARCH"].write_text(copies["RESEARCH"].read_text("utf-8").replace("<!-- hooks:begin 5.8 -->", ""), "utf-8")
    assert hookgen.stale() == [f"hook section 5.8 has no `<!-- hooks:begin 5.8 -->` table in {copies['RESEARCH'].name}"]
