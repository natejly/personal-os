"""coding_run_structured (extensions/coding/headless.py): one `claude -p` run whose answer is validated against a JSON Schema.

A fake `claude` script stands in for the CLI: it keeps its argv and stdin and prints what the test staged. The opt-in smoke test
at the end runs the real CLI (GRAIN_REAL_CLAUDE=1; it spends a little on the user's own account).

Run: PYTHONPATH=backend python -m pytest -q backend/tests/test_coding_structured.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coding_ext import call, coding_box  # noqa: E402
from personal_os.extensions.coding import headless, limited  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402

SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}, "tags": {"type": "array", "items": {"type": "string"}}},
          "required": ["n"], "additionalProperties": False}
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "claude_cli_events.json").read_text())["result"]


class Rig:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **cfg: Any) -> None:
        self.home = (tmp_path / "home").resolve()
        (self.home / "repo" / ".git").mkdir(parents=True)
        (self.home / "plain").mkdir()
        monkeypatch.setenv("HOME", str(self.home))
        self.cfg: dict[str, Any] = {"permissionMode": "allow_all", **cfg}
        self.tb, self.coding, _ = coding_box(self.cfg, workspace=Workspace(tmp_path / "data"))
        self.exe = tmp_path / "claude"
        self.exe.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$0.argv"\ncat > "$0.stdin"\n[ -f "$0.sleep" ] && sleep "$(cat "$0.sleep")"\n'
                            '[ -f "$0.err" ] && cat "$0.err" >&2\ncat "$0.out"\nexit "$(cat "$0.exit" 2>/dev/null || echo 0)"\n')
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setattr(headless, "claude_binary", lambda: str(self.exe))
        self.ctx: dict[str, Any] = {"conversation_id": "c1", "settings": self.cfg, "tainted": False, "taint_sources": []}

    def stage(self, result: Any, exit_code: int = 0, stderr: str | None = None, sleep: float | None = None) -> None:
        (Path(f"{self.exe}.out")).write_text(result if isinstance(result, str) else json.dumps(result))
        Path(f"{self.exe}.exit").write_text(str(exit_code))
        if stderr is not None:
            Path(f"{self.exe}.err").write_text(stderr)
        if sleep is not None:
            Path(f"{self.exe}.sleep").write_text(str(sleep))

    def run(self, **args: Any) -> Any:
        return call(self.tb, "coding_run_structured", self.ctx, **{"prompt": "classify", "schema": SCHEMA, **args})

    @property
    def argv(self) -> list[str]:
        return Path(f"{self.exe}.argv").read_text().splitlines()

    @property
    def stdin(self) -> str:
        return Path(f"{self.exe}.stdin").read_text()


def ok_result(**over: Any) -> dict[str, Any]:
    return {**FIXTURE, "structured_output": {"n": 7}, "num_turns": 2, **over}


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    return Rig(tmp_path, monkeypatch)


def test_the_argv_has_exactly_the_documented_flags_and_the_prompt_is_on_stdin(rig):
    rig.stage(ok_result())
    out = rig.run(prompt="List the functions in src/api.py", model="sonnet", max_budget_usd=0.25)
    assert out["ok"] is True and out["value"] == {"n": 7} and out["num_turns"] == 2 and out["permission_denials"] == []
    argv = rig.argv
    assert rig.stdin == "List the functions in src/api.py"  # a prompt never rides in the argv: --tools is variadic
    assert argv[:2] == ["-p", "--output-format"] and argv[2] == "json"
    flags = [a for a in argv if a.startswith("-")]
    assert flags == ["-p", "--output-format", "--json-schema", "--no-session-persistence", "--permission-mode", "--tools", "--allowedTools",
                     "--strict-mcp-config", "--disable-slash-commands", "--settings", "--model", "--max-budget-usd"]
    assert json.loads(argv[argv.index("--json-schema") + 1]) == SCHEMA
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--tools") + 1:argv.index("--allowedTools")] == ["Read", "Grep", "Glob"]
    assert argv[argv.index("--model") + 1] == "sonnet" and argv[argv.index("--max-budget-usd") + 1] == "0.25"
    for never in ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "--permission-prompts", "--bare", "bypassPermissions"):
        assert never not in argv
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert "deny" in settings["permissions"] and settings["hooks"]["PreToolUse"][0]["matcher"] == "*"  # the floor, and the step counter
    assert "--cap 20" in settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] and "--lifted" not in settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def test_every_flag_is_one_the_cli_lists():
    """The CLI's own --help lists each flag the run uses (skipped without the CLI)."""
    claude = shutil.which("claude")
    if not claude:
        pytest.skip("claude is not installed")
    import subprocess
    help_text = subprocess.run([claude, "--help"], capture_output=True, text=True, timeout=60).stdout
    for flag in ("-p", "--output-format", "--json-schema", "--no-session-persistence", "--permission-mode", "--tools", "--allowedTools",
                 "--strict-mcp-config", "--disable-slash-commands", "--settings", "--model", "--max-budget-usd"):
        assert flag in help_text, flag


def test_the_budget_comes_from_the_call_then_the_setting_then_a_default(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, **{"ext.coding.structuredBudgetUsd": 0.4})
    rig.stage(ok_result())
    rig.run()
    assert rig.argv[rig.argv.index("--max-budget-usd") + 1] == "0.4"
    rig.run(max_budget_usd=2)
    assert rig.argv[rig.argv.index("--max-budget-usd") + 1] == "2"
    del rig.cfg["ext.coding.structuredBudgetUsd"]
    rig.run()
    assert rig.argv[rig.argv.index("--max-budget-usd") + 1] == "1"


def test_a_budget_that_is_not_a_sane_positive_amount_is_refused_before_anything_runs(rig):
    rig.stage(ok_result())
    Path(f"{rig.exe}.argv").unlink(missing_ok=True)
    for budget in (-5, 0, 1e300, float("nan")):
        assert "max_budget_usd must be" in rig.run(max_budget_usd=budget)["error"], budget
    assert not Path(f"{rig.exe}.argv").exists()


def test_the_answer_is_only_trusted_after_five_checks_in_order(rig):
    cases = [
        ("not json at all", 1, "run_failed"),
        ('{"a": 1}\n{"b": 2}', 0, "run_failed"),
        ("[1, 2]", 0, "run_failed"),
        (ok_result(is_error=True), 1, "agent_error"),
        (ok_result(subtype="error_max_turns"), 0, "agent_error"),
        (ok_result(num_turns=30), 0, "step_cap"),  # 21 turns would be the limit for 20 steps
        (ok_result(structured_output=None), 0, "no_structured_output"),
        ({k: v for k, v in ok_result().items() if k != "structured_output"}, 0, "no_structured_output"),
        (ok_result(structured_output={"n": "seven"}), 0, "schema_mismatch"),
    ]
    for result, exit_code, error in cases:
        rig.stage(result, exit_code)
        out = rig.run()
        assert out["error"] == error and "value" not in out, (error, out)
    rig.stage(ok_result(num_turns=21))
    assert rig.run()["ok"] is True  # exactly max_steps + 1 turns is allowed
    rig.stage(ok_result(num_turns=21))
    assert rig.run(max_steps=5)["error"] == "step_cap"


def test_the_value_and_denials_are_credential_scrubbed(rig):
    secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    rig.stage(ok_result(structured_output={"n": 1, "note": f"key {secret}"}, permission_denials=[{"tool_input": {"command": f"echo {secret}"}}]))
    out = rig.run(schema={"type": "object"})
    assert secret not in json.dumps(out) and out["ok"] is True


def test_the_text_form_of_the_result_is_never_used_in_place_of_the_structured_one(rig):
    rig.stage(ok_result(structured_output=None, result='{"n": 7}'))
    assert rig.run()["error"] == "no_structured_output"
    rig.stage(ok_result(structured_output={"n": "x"}, result='{"n": 7}'))
    assert rig.run()["error"] == "schema_mismatch"


def test_a_mismatch_names_where_and_which_rule_but_not_the_value(rig):
    rig.stage(ok_result(structured_output={"n": "TOP-SECRET-VALUE", "tags": [1, "a", 2, 3, 4, 5, 6], "extra": 1}))
    out = rig.run()
    assert out["error"] == "schema_mismatch" and len(out["errors"]) == 5
    assert "TOP-SECRET-VALUE" not in json.dumps(out) and any(e.startswith("n: fails type") for e in out["errors"])


def test_a_run_that_fails_to_start_or_print_gives_the_scrubbed_stderr_tail(rig):
    rig.stage("", 2, stderr="boom " * 200 + "sk-abcdefghijklmnopqrstuvwxyz0123456789")
    out = rig.run()
    assert out["error"] == "run_failed" and len(out["stderr"]) <= headless.STDERR_TAIL and "sk-abcdef" not in out["stderr"]


def test_the_step_counter_tripping_discards_the_answer_even_if_it_validates(rig):
    with pytest.raises(headless.StructuredError) as e:
        asyncio.run(headless.interpret(json.dumps(ok_result()), SCHEMA, 20, counted=21))
    assert e.value.code == "step_cap"
    assert asyncio.run(headless.interpret(json.dumps(ok_result()), SCHEMA, 20, counted=20))["ok"] is True


def test_a_schema_pattern_that_backtracks_is_stopped_not_run_on_the_event_loop(rig, monkeypatch):
    monkeypatch.setattr(headless, "run_limited", lambda code, payload: limited.run_limited(code, payload, 1.0))
    answer = json.dumps({"subtype": "success", "structured_output": "a" * 40 + "b"})
    with pytest.raises(headless.StructuredError) as e:
        asyncio.run(headless.interpret(answer, {"type": "string", "pattern": "(a+)+$"}, 20, counted=0))
    assert e.value.code == "schema_mismatch" and "too long" in e.value.detail


def test_a_run_past_its_time_is_killed_and_reported(rig, monkeypatch):
    rig.stage(ok_result(), sleep=30)
    monkeypatch.setattr(headless, "TIMEOUT_S", (1, 900, 300))
    out = rig.run(timeout_s=1)
    assert out["error"] == "run_failed" and "did not finish within 1s" in out["detail"]


def test_a_bad_schema_is_refused_before_anything_runs(rig):
    rig.stage(ok_result())
    Path(f"{rig.exe}.argv").unlink(missing_ok=True)
    for schema, why in (({}, "JSON Schema object"), ("x", "JSON Schema object"), ({"type": "wrong"}, "not a valid"),
                        ({"type": "object", "properties": {"a": {"$ref": "https://evil.example/s.json"}}}, "only references inside"),
                        ({"allOf": [{"$ref": "file:///etc/passwd"}]}, "only references inside"),
                        ({"type": "object", "description": "x" * 17_000}, "over 16000 bytes"),
                        ({"$ref": "#"}, "refers to itself"),
                        ({"$defs": {"a": {"$ref": "#/$defs/a"}}, "$ref": "#/$defs/a"}, "refers to itself")):
        assert why in rig.run(schema=schema)["error"], schema
    assert not Path(f"{rig.exe}.argv").exists()  # nothing was spawned
    assert headless.check_schema({"type": "array", "items": {"$ref": "#"}})
    assert headless.check_schema({"$defs": {"x": {"type": "string"}}, "properties": {"a": {"$ref": "#/$defs/x"}}})["properties"]


def test_other_arguments_are_checked_too(rig):
    assert "prompt is empty" in rig.run(prompt="  ")["error"]
    assert "not a model id" in rig.run(model="no spaces allowed")["error"]
    assert "tools must be" in rig.run(tools=["Read; rm -rf /"])["error"]
    assert "tools must be" in rig.run(tools=[])["error"]


def test_a_run_that_can_write_needs_the_tool_named_and_stays_out_of_a_work_tree(rig, monkeypatch):
    rig.stage(ok_result())
    repo, plain = str(rig.home / "repo"), str(rig.home / "plain")
    assert rig.run(cwd=repo, tools=["Read"])["ok"] is True  # reading a repo is fine
    for tools in (["Edit"], ["Write"], ["Bash(git log:*)"], ["Read", "WebFetch"], ["NotebookEdit"], ["MultiEdit"], ["Task"]):
        assert "coding_session_start" in rig.run(cwd=repo, tools=tools)["error"], tools
    assert rig.run(cwd=plain, tools=["Edit", "Bash(echo:*)"])["ok"] is True
    assert rig.run(tools=["Bash(echo:*)"])["ok"] is True  # the default folder is a fresh temp one
    off = Rig(Path(rig.home).parent / "off", monkeypatch, codingRoute=False)
    off.stage(ok_result())
    assert off.run(cwd=str(off.home / "repo"), tools=["Edit"])["ok"] is True
    monkeypatch.setattr(headless, "claude_binary", lambda: str(rig.exe))
    # a worktree Grain made for a session is its own to use
    wt = rig.home / "repo" / ".claude" / "worktrees" / "feat"
    (wt).mkdir(parents=True)
    (wt / ".git").write_text("gitdir: x")
    with rig.coding.sessions.db.tx() as c:
        c.execute("INSERT INTO coding_sessions(id, agent, repo_path, worktree, branch, name, prompt, status, created_at, updated_at) "
                  "VALUES('s1','claude',?,?,'grain/x','n','p','done',1,1)", (repo, str(wt)))
    assert rig.run(cwd=str(wt), tools=["Edit"])["ok"] is True


def test_the_call_taints_the_run_by_its_schema_and_asks_when_tainted_and_is_refused_unattended(rig):
    rig.stage(ok_result())
    rig.run()
    assert rig.ctx["tainted"] and rig.ctx["taint_sources"][0].startswith("coding_run_structured:") and len(rig.ctx["taint_sources"][0]) == len("coding_run_structured:") + 8
    spec = rig.tb.specs["coding_run_structured"]
    assert (spec.danger, spec.group, spec.default) == ("external", "shell", "ask")
    assert rig.tb.forces_ask("coding_run_structured", {}, {"tainted": True}) and not rig.tb.forces_ask("coding_run_structured", {}, {})
    assert "unattended" in call(rig.tb, "coding_run_structured", {**rig.ctx, "proposal_only": True}, prompt="x", schema=SCHEMA)["error"]
    assert "not a sandbox" not in spec.description and "untrusted data" in spec.description


def test_the_temp_files_are_gone_afterwards(rig, tmp_path):
    rig.stage(ok_result())
    before = set(os.listdir(os.environ.get("TMPDIR", "/tmp")))
    rig.run()
    assert {n for n in set(os.listdir(os.environ.get("TMPDIR", "/tmp"))) - before if n.startswith("grain-structured-")} == set()


@pytest.mark.skipif(os.environ.get("GRAIN_REAL_CLAUDE") != "1" or not shutil.which("claude"),
                    reason="opt-in: set GRAIN_REAL_CLAUDE=1 (it runs the real CLI and costs a few cents)")
def test_smoke_against_the_real_cli(tmp_path, monkeypatch):
    real_home = os.environ["HOME"]  # the CLI signs in from the user's own home
    rig = Rig(tmp_path, monkeypatch)
    monkeypatch.setenv("HOME", real_home)
    monkeypatch.setattr(headless, "claude_binary", lambda: shutil.which("claude"))
    out = rig.run(prompt="Reply with n=7.", schema={"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"],
                                                      "additionalProperties": False}, max_budget_usd=0.3)
    assert out["ok"] is True and out["value"] == {"n": 7}
    for field in ("type", "subtype", "is_error", "num_turns", "total_cost_usd", "terminal_reason", "permission_denials", "result", "structured_output"):
        assert field in FIXTURE  # the fields the run reads are still the ones the CLI printed when the fixture was recorded
