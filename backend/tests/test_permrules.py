"""Permission rules: parsing, shell splitting, wrappers, hardline, path rules, session grants, the call-time fold.

Offline and pure. Run: PYTHONPATH=<repo>/backend python -m pytest -q backend/tests/test_permrules.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import permrules as pr  # noqa: E402
from personal_os.stuck import StuckDetector  # noqa: E402


def rules(allow=(), ask=(), deny=()):
    return pr.load_rules({"allow": list(allow), "ask": list(ask), "deny": list(deny)})


def ev(cmd, **kw):
    return pr.evaluate("shell_run", {"command": cmd}, rules(**{k: kw.pop(k) for k in ("allow", "ask", "deny") if k in kw}), **kw)


# ---- parsing and splitting

def test_parse_rule():
    r = pr.parse_rule("Bash(git push *)")
    assert (r.tool, r.pattern) == ("Bash", "git push *")
    assert pr.parse_rule("web_search").pattern is None
    with pytest.raises(ValueError):
        pr.parse_rule("not a rule(")


def test_split_compound_and_quotes():
    p = pr.split_command("a && b || c; d | e & f\ng")
    assert [s.words[0] for s in p.segments] == list("abcdefg") and not p.opaque
    p = pr.split_command('echo "a && b" \'c; d\'')
    assert len(p.segments) == 1 and p.segments[0].words == ["echo", "a && b", "c; d"]


@pytest.mark.parametrize("cmd", ["echo $(rm -rf /tmp/x)", "echo `id`", "cat <(ls)", "cat <<EOF\nx\nEOF", 'echo "unbalanced',
                                 "ls &&", "ls |", "(cd x && ls)", "&& ls"])
def test_opaque(cmd):
    assert pr.split_command(cmd).opaque, cmd


def test_substitution_commands_are_extracted():
    p = pr.split_command("echo $(rm -rf x)")
    assert p.opaque and any(s.words[:1] == ["rm"] for s in p.nested)
    assert ev("echo $(rm x)", deny=["Bash(rm *)"]).action == "deny"


def test_redirects_are_not_words():
    seg = pr.split_command("echo hi 2>&1 > out.txt").segments[0]
    assert seg.words == ["echo", "hi"] and seg.redirects == [(">", "out.txt")]


# ---- wrappers and normalization

def test_wrapper_stripping():
    f = pr.strip_wrappers
    assert f(["timeout", "5", "ls", "-l"]) == ["ls", "-l"]
    assert f(["time", "nice", "-n", "5", "nohup", "ls"]) == ["ls"]
    assert f(["stdbuf", "-oL", "ls"]) == ["ls"]
    assert f(["command", "ls"]) == ["ls"] and f(["xargs", "ls"]) == ["ls"]
    assert f(["LANG=C", "ls"]) == ["ls"]
    assert f(["FOO=1", "ls"]) == ["FOO=1", "ls"]            # allow rules only skip the safe variables
    assert f(["FOO=1", "ls"], True) == ["ls"]               # deny / ask rules skip any
    assert f(["xargs", "-n1", "rm"]) == ["xargs", "-n1", "rm"]
    assert f(["xargs", "-n", "1", "rm"], True) == ["rm"]
    assert f(["sudo", "-u", "root", "rm"], True) == ["rm"]


def test_normalization_closes_spellings():
    d = {"deny": ["Bash(rm *)"]}
    for cmd in ['r""m -rf x', "r\\m -rf x", "rm${IFS}-rf x", "rm$IFS-rf x", "\x1b[0mrm -rf x", "rm\\\n -rf x"]:
        assert ev(cmd, **d).action == "deny", cmd


def test_shell_c_strings_are_judged():
    assert ev("bash -c 'rm -rf x'", deny=["Bash(rm *)"]).action == "deny"
    assert ev("xargs -n1 rm", deny=["Bash(rm *)"]).action == "deny"


# ---- matching

def test_trailing_star_matches_bare_command():
    a = ["Bash(ls *)"]
    assert ev("ls", allow=a).action == "allow"
    assert ev("ls -l /tmp", allow=a).action == "allow"
    assert ev("lsof", allow=a).action is None


def test_question_mark_and_middle_star():
    assert ev("npm run test -- -k foo", allow=["Bash(npm run test *)"]).action == "allow"
    assert ev("make a", allow=["Bash(make ?)"]).action == "allow"
    assert ev("make ab", allow=["Bash(make ?)"]).action is None


def test_deny_beats_ask_beats_allow():
    assert ev("git push", allow=["Bash(git *)"], deny=["Bash(git push *)"]).action == "deny"
    assert ev("git push", allow=["Bash(git *)"], ask=["Bash(git push *)"]).action == "ask"
    assert ev("git status", allow=["Bash(git *)"], ask=["Bash(git push *)"]).action == "allow"


def test_one_unallowed_subcommand_stops_allow():
    r = ev("git status && rm -rf x", allow=["Bash(git *)"])
    assert r.action is None and r.pending == ["Bash(rm -rf x)"]
    assert ev("git status && git log", allow=["Bash(git *)"]).action == "allow"


def test_one_asked_subcommand_asks_the_line():
    r = ev("git status && git push origin", allow=["Bash(git *)"], ask=["Bash(git push *)"])
    assert r.action == "ask" and "Bash(git push origin)" in r.pending


def test_env_prefix_rules():
    assert ev("FOO=1 git status", allow=["Bash(git *)"]).action is None
    assert ev("LANG=C git status", allow=["Bash(git *)"]).action == "allow"
    assert ev("FOO=1 git push", deny=["Bash(git push *)"]).action == "deny"


def test_readonly_set():
    for c in ["ls -l", "cat a.txt", "pwd", "git status", "git diff HEAD", "git branch -a", "find . -name x", "grep -r x .", "echo hi | wc -l"]:
        assert ev(c).action == "allow", c
    for c in ["find . -delete", "find . -exec rm {} ;", "git branch -D x", "git push", "echo hi > f", "cat ~/.ssh/id_rsa", "rg --pre sh x"]:
        assert ev(c).action != "allow", c


def test_opaque_asks_even_when_allowed():
    r = ev("git status $(echo x)", allow=["Bash(git *)"])
    assert r.action == "ask" and r.kind == "opaque"


# ---- hardline

@pytest.mark.parametrize("cmd", ["rm -rf /", "rm -rf ~", "rm -fr ~/", "rm -rf $HOME", "rm -rf /*", "sudo rm -rf /usr", "rm -r --no-preserve-root x",
                                 "mkfs.ext4 /dev/sda1", "dd if=x of=/dev/disk2", "echo x > /dev/disk0", ":(){ :|:& };:", "kill -9 -1", "kill -1",
                                 "shutdown -h now", "reboot", "diskutil eraseDisk JHFS+ x disk2", "bash -c 'rm -rf /'", 'echo $(rm -rf /)'])
def test_hardline_refuses_whatever_the_rules_say(cmd):
    v = ev(cmd, allow=["Bash(*)", "Bash(rm *)", "Bash(sudo *)", "Bash(bash *)", "Bash(echo *)", "Bash(kill *)"])
    assert v.action == "deny" and v.hardline, cmd


@pytest.mark.parametrize("cmd", ["rm -rf build", "rm -rf ~/proj/build", "kill -1 1234", "rm file", "echo shutdown"])
def test_hardline_leaves_ordinary_commands(cmd):
    assert not ev(cmd).hardline


def test_resolve_hardline_even_when_mode_on():
    r = pr.resolve("shell_run", {"command": "rm -rf /"}, "on", False, rules=rules(allow=["Bash(rm *)"]))
    assert r.refusal and r.hardline


# ---- path rules

def test_path_rules_with_symlinks_and_globs(tmp_path):
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    (real / "sub" / "a.txt").write_text("x")
    (real / ".env").write_text("k")
    link = tmp_path / "link"
    os.symlink(real, link)
    r = rules(allow=[f"Read({real}/**)"], deny=[f"Read({real}/.env)", "Read(**/secret/*)"])
    assert pr.evaluate("read_local_file", {"path": str(real / "sub" / "a.txt")}, r).action == "allow"
    assert pr.evaluate("read_local_file", {"path": str(link / "sub" / "a.txt")}, r).action == "allow"   # via the symlink
    assert pr.evaluate("fs_grep", {"path": str(link / ".env")}, r).action == "deny"                    # deny resolves symlinks too
    assert pr.evaluate("read_local_file", {"path": str(real / "secret" / "k")}, r).action == "deny"
    assert pr.evaluate("read_local_file", {"path": str(tmp_path / "other.txt")}, r).action is None
    one = rules(allow=[f"Edit({real}/*)"])
    assert pr.evaluate("fs_edit", {"path": str(real / "x")}, one).action == "allow"
    assert pr.evaluate("fs_edit", {"path": str(real / "sub" / "x")}, one).action is None   # * is one segment


def test_subject_mapping_by_contract():
    s = pr.subject_for
    assert s("shell_run", {"command": "ls"}) == [pr.Subject("Bash", "ls")]
    assert s("fs_glob", {"root": "/x"}) == [pr.Subject("Read", "/x")]
    assert s("write_local_file", {"path": "/x"}) == [pr.Subject("Edit", "/x")]
    assert s("move_local_file", {"path": "/a", "to": "/b"}) == [pr.Subject("Edit", "/a"), pr.Subject("Edit", "/b")]
    assert s("agent_spawn", {"agent": "researcher"}) == [pr.Subject("Agent", "researcher")]
    assert s("web_search", {"query": "x"}) == [pr.Subject("web_search", None)]
    assert pr.evaluate("agent_spawn", {"agent": "worker"}, rules(deny=["Agent(worker)"])).action == "deny"
    assert pr.evaluate("web_search", {"q": 1}, rules(deny=["web_search"])).action == "deny"


def test_denied_path_is_refused_by_resolve(tmp_path):
    r = pr.resolve("read_local_file", {"path": str(tmp_path / "x")}, "on", False, rules=rules(deny=[f"Read({tmp_path}/**)"]))
    assert r.refusal and "Read(" in r.refusal


# ---- external directories

def test_external_directory_asks_when_a_command_touches_a_credential_store_or_grains_own_data(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    out = tmp_path / "out"
    ssh = tmp_path / ".ssh"
    data = tmp_path / "appdata"
    for d in (ws, out, ssh, data):
        d.mkdir()
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(data))
    ok = pr.evaluate("shell_run", {"command": "mkdir -p sub && touch sub/a"}, rules(allow=["Bash(mkdir *)", "Bash(touch *)"]), roots=[str(ws)])
    assert ok.action == "allow"
    # a folder outside the working folder is ordinary now: no roots, no card
    assert pr.evaluate("shell_run", {"command": f"rm {out}/x"}, rules(allow=["Bash(rm *)"]), roots=[str(ws)]).action == "allow"
    assert pr.evaluate("shell_run", {"command": f"echo hi > {out}/f"}, rules(allow=["Bash(echo *)"])).action == "allow"
    assert pr.evaluate("shell_run", {"command": "cd /"}, rules(allow=["Bash(cd *)"]), roots=[str(ws)]).action == "allow"
    assert pr.evaluate("shell_run", {"command": "rm $SOMEDIR/x"}, rules(allow=["Bash(rm *)"])).action == "allow"  # unresolved: not judged
    # a credential store (a file, not just a folder) or Grain's own data asks, with or without roots
    for cmd, target in ((f"rm {ssh}/id_rsa", ssh / "id_rsa"), (f"cp x {ws}/.env", ws / ".env"), (f"echo hi > {ssh}/config", ssh / "config"),
                        (f"rm {data}/personal-os.db", data / "personal-os.db"), (f"mv x {data}/uploads", data / "uploads")):
        for kw in ({}, {"roots": [str(ws)]}):
            v = pr.evaluate("shell_run", {"command": cmd}, rules(allow=["Bash(rm *)", "Bash(cp *)", "Bash(mv *)", "Bash(echo *)"]), **kw)
            assert v.action == "ask" and v.kind == "external_directory" and v.external == [os.path.realpath(target)], (cmd, v)
    # the card's rule suggestion and a stored allow rule still pre-approve it
    v = pr.evaluate("shell_run", {"command": f"rm {ssh}/id_rsa"}, rules(allow=["Bash(rm *)"]))
    assert v.suggestions[-1] == f"external_directory({os.path.realpath(ssh / 'id_rsa')}/**)"
    allowed = pr.evaluate("shell_run", {"command": f"rm {ssh}/id_rsa"}, rules(allow=["Bash(rm *)", f"external_directory({ssh}/**)"]))
    assert allowed.action == "allow"
    denied = pr.evaluate("shell_run", {"command": f"rm {ssh}/id_rsa"}, rules(allow=["Bash(rm *)"], deny=[f"external_directory({ssh}/**)"]))
    assert denied.action == "deny"
    assert pr.evaluate("shell_run", {"command": "chmod 755 f"}, rules(allow=["Bash(chmod *)"]), roots=[str(ws)]).action == "allow"
    assert pr.evaluate("shell_run", {"command": "rm /tmp/x"}, rules(allow=["Bash(rm *)"])).action == "allow"


# ---- suggestions

def test_arity_suggestions():
    def sug(cmd):
        return pr.evaluate("shell_run", {"command": cmd}, rules()).suggestions
    assert sug("git commit -m x") == ["Bash(git commit *)"]
    assert sug("npm run test -- -k a") == ["Bash(npm run test *)"]
    assert sug("docker compose up -d") == ["Bash(docker compose up *)"]
    assert sug("curl -s http://x") == ["Bash(curl *)"]
    assert sug("make") == ["Bash(make)"]
    assert sug("a1 x && b1 y && c1 && d1 && e1 && f1 z") == ["Bash(a1 *)", "Bash(b1 *)", "Bash(c1)", "Bash(d1)", "Bash(e1)"]
    assert sug("ls && echo $(x)") == []


def test_validate_saved_rules():
    args = {"command": "git commit -m x"}
    assert pr.validate_saved_rules("shell_run", args, ["Bash(git commit *)"]) == ["Bash(git commit *)"]
    for bad in (["Bash(*)"], ["Bash"], ["Read(/x/**)"], ["nope("]):
        with pytest.raises(ValueError):
            pr.validate_saved_rules("shell_run", args, bad)


# ---- call-time fold, session grants, forced

def test_resolve_downgrade_and_force():
    rs = rules(allow=["Bash(npm run test *)"], ask=["Bash(git push *)"])
    run = {"command": "npm run test -- -k foo"}
    assert pr.resolve("shell_run", run, "ask", False, rules=rs).mode == "on"
    assert pr.resolve("shell_run", run, "ask", True, rules=rs).mode == "ask"                # forced: never downgraded
    assert pr.resolve("shell_run", {"command": "git push"}, "on", False, rules=rs).mode == "ask"
    assert pr.resolve("shell_run", {"command": "git status && rm -rf x"}, "ask", False, rules=rules(allow=["Bash(git *)"])).mode == "ask"
    assert pr.resolve("shell_run", {"command": "x"}, "off", False, rules=rules(deny=["Bash(x)"])).mode == "off"
    c = pr.resolve("shell_run", {"command": "git push"}, "ask", False, rules=rs).card()
    assert c and c["suggestions"] == ["Bash(git push)"] and c["rule"] == "Bash(git push *)" and c["session"]


def test_session_grants_are_scoped():
    pr.SESSION.clear()
    rs = rules()
    a = {"command": "make deploy"}
    assert pr.resolve("shell_run", a, "ask", False, rules=rs, conv="c1").mode == "ask"
    pr.SESSION.add("c1", pr.resolve("shell_run", a, "ask", False, rules=rs, conv="c1").keys)
    assert pr.resolve("shell_run", a, "ask", False, rules=rs, conv="c1").mode == "on"
    assert pr.resolve("shell_run", a, "ask", False, rules=rs, conv="c2").mode == "ask"                  # other chat
    assert pr.resolve("shell_run", {"command": "make clean"}, "ask", False, rules=rs, conv="c1").mode == "ask"  # other command
    assert pr.resolve("shell_run", a, "ask", True, rules=rs, conv="c1").mode == "ask"                   # forced
    # a plain tool is granted by name
    k = pr.resolve("web_search", {"query": "x"}, "ask", False, rules=rs, conv="c1").keys
    assert k == ["web_search"]
    pr.SESSION.add("c1", k)
    assert pr.resolve("web_search", {"query": "y"}, "ask", False, rules=rs, conv="c1").mode == "on"
    pr.SESSION.clear("c1")
    assert pr.resolve("web_search", {"query": "y"}, "ask", False, rules=rs, conv="c1").mode == "ask"


def test_doom_loop_cannot_be_downgraded():
    r = pr.resolve("fs_grep", {"path": "/x"}, "on", False, rules=rules(allow=["fs_grep", "Read(/x)"]), conv="c", doom=True)
    assert r.mode == "ask" and r.forced and r.display == "doom_loop(fs_grep)" and r.kind == "doom_loop"
    pr.SESSION.add("c", ["doom_loop(fs_grep)", "fs_grep"])
    assert pr.resolve("fs_grep", {"path": "/x"}, "on", False, rules=rules(allow=["fs_grep"]), conv="c", doom=True).mode == "ask"
    # a deny still wins over the card
    assert pr.resolve("fs_grep", {"path": "/x"}, "on", False, rules=rules(deny=["fs_grep"]), doom=True).refusal


def test_stuck_detector_counts_identical_calls():
    d = StuckDetector()
    args = {"q": "x"}
    for _ in range(2):
        d.observe("fake_tool", args, {"ok": 1})
    assert d.repeat_count("fake_tool", args) == 2
    assert d.repeat_count("fake_tool", {"q": "y"}) == 0
    d.observe("other", {}, {})
    assert d.repeat_count("fake_tool", args) == 0


def test_denial_streak():
    s = pr.DenialStreak()
    for _ in range(3):
        assert s.note() is None
        s.record(True)
    assert "ask how they would like" in (s.note() or "")
    s.record(False)
    assert s.note() is None


def test_fork_bombs_are_hardline_and_a_long_command_is_not_slow():
    import time
    for bomb in (":(){ :|:& };:", "bomb(){ bomb|bomb& };bomb", "x bomb ( ) { bomb | bomb & }"):
        assert pr.hardline(bomb) == "a fork bomb"
    assert pr.hardline("f(){f|f}") is None
    t = time.time()  # the old pattern took minutes on 150 KB of one word (quadratic backtracking)
    assert pr.hardline("echo " + "x" * 150_000 + " > big.txt; touch huge.txt") is None
    assert pr.hardline("(){" * 50_000) is None
    assert time.time() - t < 5


def test_malformed_rules_are_skipped():
    rs = pr.load_rules({"allow": ["Bash(ls *)", "((", 5], "deny": "nope"})
    assert [r.text for r in rs.allow] == ["Bash(ls *)"] and not rs.deny
    assert pr.load_rules(None).empty()
