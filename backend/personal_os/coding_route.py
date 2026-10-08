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
A repo whose root is the home folder (a dotfiles checkout) does not count, or every file under ~ would be refused.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from . import permissions, permrules

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


def check(tb: Any, name: str, args: dict[str, Any], ctx: dict[str, Any]) -> str | None:
    """The refusal for this call while coding is routed to a coding agent, or None. Called by Toolbox.precheck (before
    any card) and Toolbox.call (every path that runs a tool)."""
    if not isinstance(args, dict):
        return None
    cfg = ctx.get("settings") or tb.settings()
    if not enabled(cfg):
        return None
    from . import fsx, shell
    if name in fsx.WRITE_ARGS:
        g = fsx.grants_for(tb, ctx)
        base = str(g.desk) if g.desk and name not in fsx.LOCAL_TOOLS else str(Path.home())
        for key in fsx.WRITE_ARGS[name]:
            raw = args.get(key)
            if isinstance(raw, str) and raw.strip() and (root := repo_root(_resolve(raw.strip(), base))):
                return refusal_text(cfg, name, root)
        return None
    if name == "shell_run":
        roots, start = shell.perm_where(tb, ctx)
        raw = os.path.expanduser(str(args.get("cwd") or "").strip())
        cwd = os.path.join(roots[0] if roots else str(Path.home()), raw) if raw else start  # as shell.floor judges it
        if hit := _shell_refusal(str(args.get("command") or ""), cwd):
            return refusal_text(cfg, f"shell_run ({hit[0]})", hit[1])
    return None
