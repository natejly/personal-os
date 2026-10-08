"""Coding goes to OpenCode or Claude Code: Grain's own agents plan, brief, delegate and verify; they do not hand-edit repos.

While the `codingRoute` permission setting is on (the default), a call from any Grain agent (chat, desk, worker, subagent,
workflow step, a run_python tool bridge) that would change files inside a git work tree is refused, under every
permission mode including Allow everything. The refusal tells the agent to start a coding session instead, naming the
agent `codingAgent` prefers. This only adds refusals: the rules, the hardline list and the Allow-everything floor are
decided where they always were, and none of them is consulted or lifted here.

What counts as changing a repo:
- Grain's file writers (fsx.WRITE_ARGS: fs_edit, fs_copy's destination, fs_mkdir, write_local_file, move_local_file's both
  ends, trash_local_file) whose resolved path is inside a work tree.
- A shell_run subcommand, judged from where it runs (its `cwd`, then each `cd X`, `git -C X`, absolute paths): an output
  redirect, a file-changing command (rm, mv, cp, touch, mkdir, tee, ln, patch, ...), an in-place edit (sed/perl -i), a git
  subcommand that is not read-only (commit, checkout, switch, merge, rebase, reset, stash, apply, add, ...), a package
  manager changing dependencies (npm install, yarn add, uv add, ...), a formatter or linter told to fix or write
  (--fix, --write, black, ruff format, gofmt -w), and inline interpreter code that writes or deletes files.
Allowed in a repo: reading (ls, cat, rg, git status/diff/log/show/fetch, branch listing), running tests, typecheck, lint
and builds (npm test / npm run X, pytest, tsc, eslint without --fix), and publishing what a coding session produced:
git push and gh (pr create, pr merge, ...) are not file edits, so the main agent can land a session's branch.
Not judged here at all: opencode_run, coding_session_*, the coding-agent MCP connectors and whatever those agents run,
desk_* workspace tools (the desk workspace is Grain's data folder, never a user's repo) and run_python (sandboxed to the
desk workspace or a temp folder).

A command the parser cannot follow is refused only when it runs in a repo and its raw text has a write-ish verb.

The judging above is a heuristic: a script file, a heredoc or code it cannot read can still write. So a shell_run that
runs in a repo (its cwd, or a folder its words name) is also checked after the fact (`guard_start` / `guard_finish`):
`git status` before and after, and a source file the command newly changed is put back (a tracked file from HEAD, a new
file deleted) and the call refused. Files that were already modified before it are never touched, only named. Gitignored
paths, caches, build output and lockfiles do not count, and a command that only tests, builds or installs is not
snapshotted at all.
A repo whose root is the home folder (a dotfiles checkout) does not count, or every file under ~ would be refused.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from . import permissions, permrules

log = logging.getLogger(__name__)

AGENT_NAMES = {"opencode": "OpenCode", "claude": "Claude Code"}

# Commands whose operands are all changed; and the ones whose last operand is the one written.
ALL_OPERANDS = {"rm", "rmdir", "mv", "touch", "mkdir", "chmod", "chown", "tee", "truncate", "unlink", "shred", "patch"}
LAST_OPERAND = {"cp", "ln", "install", "rsync", "ditto"}
IN_PLACE = {"sed", "gsed", "perl", "ruby"}
# An install that only reproduces the lockfile (npm ci is not in PKG_WRITES either): what verifying a session needs.
LOCKED_INSTALL = {"--frozen-lockfile", "--immutable", "--locked", "--frozen", "--no-save", "--ci"}
# git subcommands that change nothing in the work tree. Anything else (commit, checkout, switch, merge, rebase, reset,
# stash, apply, am, add, rm, mv, restore, clean, pull, cherry-pick, revert, ...) is a write.
GIT_READ = {"status", "diff", "log", "show", "fetch", "blame", "grep", "ls-files", "ls-tree", "ls-remote", "rev-parse",
            "rev-list", "describe", "shortlog", "cat-file", "for-each-ref", "show-ref", "reflog", "help", "version",
            "push", "remote", "config", "merge-base", "name-rev", "count-objects", "check-ignore", "var", "whatchanged"}
GIT_LIST_ONLY = {"branch": permrules.GIT_BRANCH_FLAGS | {"--merged", "--no-merged", "--contains", "--sort", "--format"},
                 "tag": {"-l", "--list", "-n", "--sort", "--contains"},
                 "stash": {"list", "show"}, "worktree": {"list"}}
GIT_GLOBAL_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
PKG_WRITES = {"npm": {"install", "i", "add", "uninstall", "remove", "rm", "un", "update", "up", "upgrade", "dedupe",
                      "link", "prune", "version", "init"},
              "yarn": {"add", "remove", "upgrade", "install", "up", "dedupe", "init", "version"},
              "pnpm": {"add", "remove", "rm", "install", "i", "update", "up", "upgrade", "dedupe", "init", "link"},
              "bun": {"add", "remove", "rm", "install", "i", "update", "init", "link"},
              "uv": {"add", "remove", "lock", "init", "version"},
              "poetry": {"add", "remove", "lock", "init", "version"},
              "cargo": {"add", "remove", "init", "new", "fix", "fmt", "update"},
              "go": {"get", "mod", "fmt", "generate"}}
FIX_FLAGS = {"--fix", "--write", "--in-place", "--apply"}
FORMATTERS = {"black", "isort", "autopep8", "autoflake", "yapf", "gofmt", "rustfmt", "clang-format", "prettier", "biome"}
CHECK_FLAGS = {"--check", "--diff", "-l", "--list-different", "-d", "--dry-run"}
INTERPRETERS = {"python", "python3", "node", "deno", "bun", "perl", "ruby", "osascript"}
INLINE_FLAGS = {"-c", "-e", "--eval", "-p", "--print"}
WRITE_CODE = re.compile(r"\.write|write_text|write_bytes|writeFile|appendFile|open\([^)]*['\"][wax+]|unlink|rmtree|"
                        r"remove\(|rename\(|mkdir|copyfile|shutil\.|fs\.\w+Sync\(")
# For a command the parser gives up on: words that may change files.
WRITE_VERB = re.compile(r"(?:^|[\s;&|(`])(?:rm|mv|cp|touch|mkdir|tee|ln|patch|truncate|sed\s+-i|perl\s+-\w*i|"
                        r"git\s+(?:commit|checkout|switch|merge|rebase|reset|stash|apply|am|add|rm|mv|restore|clean|pull|"
                        r"cherry-pick|revert)|npm\s+(?:i|install|add)|yarn\s+add|pnpm\s+add)\b|(?<![\d&=-])>>?(?!&)")


def enabled(cfg: dict[str, Any] | None) -> bool:
    return bool(permissions.get(cfg or {}, "codingRoute"))


def agent_choice(cfg: dict[str, Any] | None) -> str:
    """'opencode' | 'claude' | 'any'."""
    v = permissions.get(cfg or {}, "codingAgent")
    return v if v in AGENT_NAMES else "any"


def _agent_phrase(cfg: dict[str, Any] | None) -> str:
    a = agent_choice(cfg)
    return (f"agent='{a}' ({AGENT_NAMES[a]}, the user's preferred coding agent)" if a != "any"
            else "agent='opencode' (OpenCode) or agent='claude' (Claude Code), whichever suits the task")


def hint(cfg: dict[str, Any] | None) -> str:
    """The prompt rule, for the main agent, desks, workers and subagents alike. Empty while the setting is off."""
    if not enabled(cfg):
        return ""
    return ("## Coding goes to a coding agent\n"
            "Any coding task (writing or changing code, config or tests in a git repository) is done by a coding agent, never by "
            "you editing files or running file-changing commands in the repo; those calls are refused. You plan, delegate and "
            f"verify: find the repo and what the change touches, then call coding_session_start with {_agent_phrase(cfg)}, the "
            "absolute repo_path, new_worktree=true, and a self-contained brief (goal, files and context, constraints, how to "
            "test, what to report), because the coding agent cannot see this conversation. Follow it with coding_session_status, "
            "then review the result yourself with coding_session_diff and by running the tests, typecheck or build in its "
            "worktree, and send a follow-up with coding_session_send if something is wrong. opencode_run (OpenCode in the "
            "foreground) is also allowed for a small, quick change. Reading, searching and running tests in a repo, and "
            "git push / gh pr to publish a session's branch, stay yours.")


def refusal_text(cfg: dict[str, Any] | None, what: str, root: str) -> str:
    return (f"{what} would change files in the git repository {root}, and coding is routed to a coding agent (Settings → "
            "Advanced → Coding sessions → Route coding to OpenCode / Claude Code). Hand the change to coding_session_start "
            f"with {_agent_phrase(cfg)}, repo_path='{root}', new_worktree=true and a self-contained brief (goal, files, "
            "constraints, how to test); then check coding_session_diff and run the tests yourself. Reading files, running "
            "tests and git push / gh are still allowed here.")


def repo_root(path: str) -> str | None:
    """The work tree `path` (existing or not yet) sits in: the nearest ancestor holding a `.git` folder or file."""
    home = os.path.realpath(os.path.expanduser("~"))
    p = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return None if p in (home, "/") else p  # ponytail: a dotfiles repo at ~ is ignored, not judged per file
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def _resolve(w: str, here: str) -> str:
    w = permrules._expand_home(w)
    return os.path.normpath(w if os.path.isabs(w) else os.path.join(here, w))


def _operands(tokens: list[str]) -> list[str]:
    return [t for t in tokens[1:] if not t.startswith("-")]


def _git_write(tokens: list[str], here: str) -> tuple[bool, str]:
    """(is a write, the folder it acts on) for a git command: `-C X` moves the folder, global options are skipped."""
    i, where = 1, here
    while i < len(tokens) and tokens[i].startswith("-"):
        flag = tokens[i]
        if flag in GIT_GLOBAL_ARG and i + 1 < len(tokens):
            if flag == "-C":
                where = _resolve(tokens[i + 1], where)
            elif flag == "--work-tree":
                where = _resolve(tokens[i + 1], here)
            i += 2
        else:
            if flag.startswith("--work-tree="):
                where = _resolve(flag.split("=", 1)[1], here)
            i += 1
    if i >= len(tokens):
        return False, where
    sub, rest = tokens[i], tokens[i + 1:]
    if sub in GIT_LIST_ONLY:
        allowed = GIT_LIST_ONLY[sub]
        if not rest:  # bare `git stash` stashes; bare `git branch` / `git tag` list
            return sub in ("stash", "worktree"), where
        return not all(a.split("=", 1)[0] in allowed for a in rest), where
    if sub == "remote":
        return bool(rest) and rest[0] not in ("-v", "show", "get-url"), where
    if sub == "config":
        return not any(a in ("--get", "--get-all", "--list", "-l", "--get-regexp") for a in rest), where
    return sub not in GIT_READ, where


def _segment_targets(tokens: list[str], redirects: list[tuple[str, str]], here: str) -> tuple[str, list[str]] | None:
    """(what, paths it changes) for one subcommand, or None when it changes no file. Paths are resolved from `here`."""
    outs = [_resolve(t, here) for op, t in redirects if op.startswith((">", "&>")) and t not in permrules.EXEMPT_PATHS]
    if outs:
        return "a redirect", outs
    if not tokens:
        return None
    name = os.path.basename(tokens[0])
    ops = _operands(tokens)
    if name in ALL_OPERANDS:
        return name, [_resolve(o, here) for o in ops] or [here]
    if name in LAST_OPERAND:
        return name, [_resolve(ops[-1], here)] if ops else [here]
    if name == "dd":
        of = [t[3:] for t in tokens[1:] if t.startswith("of=")]
        return ("dd", [_resolve(of[0], here)]) if of else None
    if name in IN_PLACE and any(t == "--in-place" or re.fullmatch(r"-[a-zA-Z]*i\S*", t) for t in tokens[1:]):
        return f"{name} -i", [_resolve(o, here) for o in ops[1:]] or [here]
    if name == "git":
        write, where = _git_write(tokens, here)
        return ("git " + next((t for t in tokens[1:] if not t.startswith("-")), ""), [where]) if write else None
    if name == "gh":
        return ("gh pr checkout", [here]) if tokens[1:3] == ["pr", "checkout"] else None
    if name in PKG_WRITES and len(tokens) > 1 and tokens[1] in PKG_WRITES[name] and not LOCKED_INSTALL & set(tokens):
        return f"{name} {tokens[1]}", [here]
    if name in ("ruff", "eslint", "stylelint", "biome", "prettier", "rubocop", "swiftlint", "golangci-lint") and \
            (FIX_FLAGS & set(tokens) or "-w" in tokens or (name == "ruff" and "format" in tokens and "--check" not in tokens)):
        return f"{name} fix", [here]
    if name in FORMATTERS and name not in ("prettier", "biome") and not CHECK_FLAGS & set(tokens) and \
            (name not in ("gofmt", "clang-format", "rustfmt") or "-w" in tokens or "-i" in tokens or name == "rustfmt"):
        return name, [here]
    if re.fullmatch(r"(python|node|perl|ruby)[\d.]*", name) or name in INTERPRETERS:
        code = next((tokens[j + 1] for j, t in enumerate(tokens[:-1]) if t in INLINE_FLAGS), None)
        if code is not None and WRITE_CODE.search(code):
            return f"{name} inline code", [here]
    return None


def _shell_refusal(cmd: str, start: str) -> tuple[str, str] | None:
    """(what, repo root) for the first subcommand of `cmd` that changes files in a work tree, judged from `start`."""
    def walk(text: str, depth: int, here: str) -> tuple[str, str] | None:
        if depth > 4:
            return None
        p = permrules.split_command(text)
        segs = p.segments + p.nested
        if p.opaque:
            segs = segs + permrules.split_command(re.sub(r"[(){}`]", ";", text)).segments
        for seg in segs:
            tokens = permrules.strip_wrappers(seg.words, True)
            if (inner := permrules._shell_c(tokens)) is not None and (hit := walk(inner, depth + 1, here)):
                return hit
            if (found := _segment_targets(tokens, seg.redirects, here)) is not None:
                what, paths = found
                for path in paths:
                    if root := repo_root(path):
                        return what, root
            if (to := permrules._cd_to(tokens, here)) is not None:
                here = to
        # A line the parser could not follow fully: refuse when it runs in a repo and reads like it writes.
        if p.opaque and (root := repo_root(here)) and WRITE_VERB.search(text):
            return "a command that could not be fully parsed", root
        return None
    return walk(permrules.normalize(cmd or ""), 0, start)


def _shell_cwd(tb: Any, args: dict[str, Any], ctx: dict[str, Any]) -> str:
    from . import shell
    roots, start = shell.perm_where(tb, ctx)
    raw = os.path.expanduser(str(args.get("cwd") or "").strip())
    return os.path.join(roots[0] if roots else str(Path.home()), raw) if raw else start  # as shell.floor judges it


# ---- after the fact: what a shell_run really changed in a repo ----
# Not snapshotted: a command whose every subcommand tests, builds or installs (or only reads, around one of those).
VERIFY = {"pytest", "py.test", "tsc", "vitest", "jest", "eslint", "mypy", "make", "tox", "nox", "cmake", "ninja", "mvn",
          "gradle", "gradlew", "xcodebuild", "playwright"}
VERIFY_SUB = {"npm": {"test", "t", "ci", "run", "run-script", "build"}, "pnpm": {"test", "t", "run", "build"},
              "yarn": {"test", "run", "build"}, "bun": {"test", "run"}, "cargo": {"build", "test", "check", "clippy", "bench"},
              "go": {"build", "test", "vet"}, "swift": {"build", "test"}, "uv": {"sync"}, "poetry": {"install"},
              "bundle": {"install"}, "pip": {"install"}, "pip3": {"install"}}
RUNNERS = {"npx", "pnpx", "bunx"}  # run the tool named next: judged as that tool
BENIGN = permrules.READONLY | {"cd", "true", "sort", "uniq", "tr", "cut", "less", "env", "printf", "sleep"}
# Changes that are never source edits, even when not gitignored.
NOT_SOURCE_DIRS = {"node_modules", "dist", "build", "out", "coverage", "__pycache__", ".pytest_cache", ".mypy_cache",
                   ".ruff_cache", ".next", ".turbo", ".cache", ".venv", "venv", "target", ".tox", ".nyc_output", ".gradle"}
LOCKFILES = {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock", "uv.lock",
             "poetry.lock", "Cargo.lock", "Gemfile.lock", "go.sum", "Pipfile.lock"}
NOT_SOURCE_SUFFIX = (".pyc", ".tsbuildinfo", ".log", ".DS_Store")
GIT_TIMEOUT = 10


def _verifying(tokens: list[str]) -> bool:
    if not tokens:
        return False
    name = os.path.basename(tokens[0])
    if name in RUNNERS or (name in ("uv", "poetry") and tokens[1:2] == ["run"]):
        rest = tokens[2:] if name in ("uv", "poetry") else tokens[1:]
        while rest and rest[0].startswith("-"):
            rest = rest[1:]
        return _verifying(rest)
    if re.fullmatch(r"python[\d.]*", name):
        return tokens[1:2] == ["-m"] and tokens[2:3] in (["pytest"], ["unittest"], ["mypy"], ["compileall"])
    return name in VERIFY or (len(tokens) > 1 and tokens[1] in VERIFY_SUB.get(name, ()))


def _only_verifies(cmd: str) -> bool:
    p = permrules.split_command(cmd)
    if p.opaque or p.nested:
        return False
    kinds = []
    for seg in p.segments:
        tokens = permrules.strip_wrappers(seg.words, True)
        if any(op.startswith((">", "&>")) and t not in permrules.EXEMPT_PATHS for op, t in seg.redirects):
            return False
        kinds.append("v" if _verifying(tokens) else "b" if tokens and os.path.basename(tokens[0]) in BENIGN else "x")
    return "v" in kinds and "x" not in kinds


def _git(root: str, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "--literal-pathspecs", "-C", root, *args], input=stdin, capture_output=True, text=True,
                          timeout=GIT_TIMEOUT, check=True)


def _status(root: str) -> dict[str, str]:
    """path -> XY for every non-ignored change in the work tree (untracked files listed one by one)."""
    out = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames").stdout
    return {e[3:]: e[:2] for e in out.split("\0") if len(e) > 3}


def _stat(path: str) -> tuple[int, int] | None:
    try:
        st = os.lstat(path)
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def _source(path: str) -> bool:
    parts = path.split("/")
    return not (NOT_SOURCE_DIRS & set(parts[:-1]) or parts[-1] in LOCKFILES or parts[-1].endswith(NOT_SOURCE_SUFFIX))


def guard_start(tb: Any, args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any] | None:
    """The repos a foreground shell_run may touch, with what was already modified in each, or None when there is nothing
    to watch: the setting is off, it runs in no repo, or it only tests / builds / installs."""
    if not isinstance(args, dict) or args.get("background"):  # ponytail: a background job is not diffed when it ends; only the precheck judges it
        return None
    cfg = ctx.get("settings") or tb.settings()
    if not enabled(cfg):
        return None
    cmd = permrules.normalize(str(args.get("command") or ""))
    if not cmd.strip() or _only_verifies(cmd):
        return None
    cwd = _shell_cwd(tb, args, ctx)
    # Where it may write: its cwd, and any folder its words name (`cd ~/repo && ...`, `python ~/repo/x.py`).
    words = re.findall(r"[^\s'\"`;&|()<>=]+", cmd)
    cands = [cwd] + [_resolve(w, cwd) for w in words if "/" in w or w.startswith("~") or w in (".", "..")
                     or os.path.isdir(os.path.join(cwd, w))]
    roots = list(dict.fromkeys(r for c in cands if (r := repo_root(c))))[:4]
    snap: dict[str, Any] = {"cfg": cfg, "repos": {}}
    for root in roots:
        try:
            before = _status(root)
        except (OSError, subprocess.SubprocessError):
            log.warning("coding_route: git status failed in %s; not guarding it", root, exc_info=True)
            continue
        snap["repos"][root] = {p: (xy, _stat(os.path.join(root, p))) for p, xy in before.items()}
    return snap if snap["repos"] else None


def guard_finish(snap: dict[str, Any]) -> str | None:
    """Put back the source files the command newly changed and return the refusal, or None when it changed none."""
    for root, before in snap["repos"].items():
        try:
            after = _status(root)
        except (OSError, subprocess.SubprocessError):
            log.warning("coding_route: git status failed in %s after a command", root, exc_info=True)
            continue
        new = [p for p in after if p not in before and _source(p)]
        # Already modified before the command: never touched, only named when the command changed them further.
        also = [p for p, (xy, st) in before.items() if _source(p) and (after.get(p) != xy or _stat(os.path.join(root, p)) != st)]
        if not new and not also:
            continue
        reverted, failed = [], []
        if new:
            try:
                check = _git(root, "cat-file", "--batch-check", stdin="".join(f"HEAD:{p}\n" for p in new)).stdout.splitlines()
                in_head = [p for p, line in zip(new, check) if not line.endswith(" missing")]
                if in_head:
                    _git(root, "restore", "--source=HEAD", "--staged", "--worktree", "--", *in_head)
                fresh = [p for p in new if p not in in_head]
                if fresh:
                    _git(root, "rm", "-q", "--cached", "--ignore-unmatch", "--", *fresh)
                    for p in fresh:
                        f = os.path.join(root, p)
                        if os.path.lexists(f) and not os.path.isdir(f):
                            os.remove(f)
                reverted = new
            except (OSError, subprocess.SubprocessError):
                log.warning("coding_route: could not revert %s in %s", new, root, exc_info=True)
                failed = new
        def names(ps: list[str]) -> str:
            return ", ".join(ps[:8]) + (f" and {len(ps) - 8} more" if len(ps) > 8 else "")
        msg = refusal_text(snap["cfg"], "This shell_run", root)
        if reverted:
            msg += f" It changed {names(reverted)}; those changes were reverted."
        if failed:
            msg += f" It changed {names(failed)}, and putting them back failed: check `git status` there."
        if also:
            msg += (f" It also changed {names(also)}, which already had uncommitted changes, so they were left as they are; "
                    "tell the user.")
        return msg
    return None


def check(tb: Any, name: str, args: dict[str, Any], ctx: dict[str, Any]) -> str | None:
    """The refusal for this call while coding is routed to a coding agent, or None. Called by Toolbox.precheck (before
    any card) and Toolbox.call (every path that runs a tool)."""
    if not isinstance(args, dict):
        return None
    cfg = ctx.get("settings") or tb.settings()
    if not enabled(cfg):
        return None
    from . import fsx
    if name in fsx.WRITE_ARGS:
        g = fsx.grants_for(tb, ctx)
        base = str(g.desk) if g.desk and name not in fsx.LOCAL_TOOLS else str(Path.home())
        for key in fsx.WRITE_ARGS[name]:
            raw = args.get(key)
            if isinstance(raw, str) and raw.strip() and (root := repo_root(_resolve(raw.strip(), base))):
                return refusal_text(cfg, name, root)
        return None
    if name == "shell_run":
        if hit := _shell_refusal(str(args.get("command") or ""), _shell_cwd(tb, args, ctx)):
            return refusal_text(cfg, f"shell_run ({hit[0]})", hit[1])
    return None
