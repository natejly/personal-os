"""Permission rules: argument-pattern allow / ask / deny lists layered on top of the per-tool modes.

A rule is `Tool` or `Tool(pattern)`. The subjects a call is matched on follow one contract:
`Bash(<command>)` for shell_run, `Read(<path>)` for read_local_file / fs_glob / fs_grep, `Edit(<path>)` for the
tools that change files, `Agent(<type>)` for agent_spawn, and the bare tool name for everything else.

Evaluation is deny, then ask, then allow, and a shell command is split into its subcommands first: one denied
or asked subcommand decides the whole line, and every subcommand has to be allowed (by a rule or the fixed
read-only set) for the line to be allowed. These are conveniences, not a boundary: the sandbox is the boundary,
and `HARDLINE` is the one list that nothing (no rule, no approval) lifts.

Pure: no app imports and no I/O beyond resolving paths. `resolve` is what the chat loop calls for each call.
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from email.utils import getaddresses
from typing import Any, Iterable

from . import permissions

# Rule names that are not tool names: the subject kinds `subject_for` emits.
PSEUDO_TOOLS = frozenset({"Bash", "Read", "Edit", "Agent", "external_directory"})
# Verdicts: 'deny' | 'ask' | 'allow' | None (no opinion: the tool's own mode stands).
MAX_SUGGESTIONS = 5
# Matches a call that has just been refused this many times in a row (permission refusals and user denials).
DENIAL_LIMIT = 3
# The call that would be this many identical ones in a row (counting those that ran) gets a card no rule lifts.
DOOM_LIMIT = 3
# Cards that are the user answering, not granting a tool. Skip-permissions does not settle these.
STILL_ASK = frozenset({"propose_plan", "desk_ask", "ask_user", "gmail_send"})  # gmail_send: the email card is the user writing, not granting
HARD_STOP = ("Three calls in a row were refused. Stop attempting variations of them; tell the user what you were trying "
             "to do and ask how they would like to proceed.")

READ_TOOLS = {"read_local_file", "fs_glob", "fs_grep"}
EDIT_TOOLS = {"write_local_file", "fs_edit", "fs_copy", "fs_mkdir", "move_local_file", "trash_local_file"}
MAIL_TOOLS = {"gmail_send", "gmail_draft"}
CALENDAR_TOOLS = {"calendar_create", "calendar_update", "calendar_delete", "calendar_propose"}
PATH_KEYS = ("path", "root", "directory", "dir", "file_path", "folder")
SRC_KEYS = ("src", "source", "from")
DEST_KEYS = ("dest", "dst", "destination", "to", "target")

# Commands whose path arguments are looked at for the outside-the-workspace check.
PATH_COMMANDS = {"cd", "rm", "cp", "mv", "mkdir", "touch", "chmod", "chown", "ln", "tee"}
# These take a mode or an owner before the paths.
LEADING_ARG_COMMANDS = {"chmod", "chown"}
EXEMPT_PATHS = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/stdin", "/dev/tty")

READONLY = {"ls", "cat", "head", "tail", "wc", "pwd", "echo", "which", "file", "stat", "grep", "rg", "find"}
GIT_READONLY = {"status", "diff", "log", "show", "branch"}
GIT_BRANCH_FLAGS = {"-a", "-r", "-v", "-vv", "--list", "--show-current", "--all", "--remotes"}
FIND_WRITERS = {"-exec", "-execdir", "-delete", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
SENSITIVE = re.compile(r"(^|/)(\.ssh|\.aws|\.gnupg|\.netrc|\.env[^/]*|Keychains|id_(rsa|ed25519|ecdsa|dsa)[^/]*)(/|$)")

# Wrappers that run what follows. Allow rules see through the conservative set only; deny and ask rules also
# see through the ones that could be hiding a destructive command.
SAFE_ENV = re.compile(r"^(LANG|LC_[A-Z_]+|TZ|NO_COLOR)=")
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
XARGS_ARG_FLAGS = {"-n", "-I", "-P", "-L", "-s", "-E", "-d", "-a", "-J"}

# Subcommand-taking programs, for suggesting a rule that is narrow but reusable (`git commit *`).
ARITY = {"git": 2, "npm": 2, "yarn": 2, "pnpm": 2, "cargo": 2, "docker": 2, "kubectl": 2, "uv": 2, "pip": 2}
ARITY_THREE = {("npm", "run"), ("pnpm", "run"), ("yarn", "run"), ("docker", "compose"), ("uv", "run")}


# ---------------------------------------------------------------- rules

@dataclass(frozen=True)
class Rule:
    tool: str
    pattern: str | None
    text: str


_RULE = re.compile(r"^\s*([A-Za-z_][\w.\-]*)\s*(?:\((.*)\))?\s*$", re.S)


def parse_rule(text: str) -> Rule:
    """`Bash(git push *)` -> Rule('Bash', 'git push *'). Raises ValueError on anything that is not one."""
    m = _RULE.match(text or "")
    if not m:
        raise ValueError(f"not a rule: {text!r}")
    pat = m.group(2)
    if pat is not None:
        pat = pat.strip()
    return Rule(m.group(1), pat if pat else None, f"{m.group(1)}({pat})" if pat else m.group(1))


@dataclass
class RuleSet:
    allow: list[Rule] = field(default_factory=list)
    ask: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.allow or self.ask or self.deny)


def load_rules(raw: Any) -> RuleSet:
    """The `permissionRules` setting as a RuleSet. Tolerant: a malformed entry is skipped, not fatal."""
    out = RuleSet()
    if not isinstance(raw, dict):
        return out
    for key in ("allow", "ask", "deny"):
        items = raw.get(key)
        for t in items if isinstance(items, list) else []:
            try:
                getattr(out, key).append(parse_rule(str(t)))
            except ValueError:
                continue
    return out


def _glob_re(pat: str, path: bool) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        if c == "*":
            if path and pat[i:i + 2] == "**":
                if pat[i:i + 3] == "**/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*" if path else ".*")
        elif c == "?":
            out.append("[^/]" if path else ".")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out), re.S)


def _cmd_match(pattern: str, text: str) -> bool:
    """Glob over a command line. A trailing ' *' also matches the bare command, so `ls *` is `ls` or `ls x`, never `lsof`."""
    pattern = " ".join(pattern.split())
    if pattern.endswith(" *"):
        base = _glob_re(pattern[:-2], False).pattern
        return re.fullmatch(f"{base}(?: .*)?", text, re.S) is not None
    return _glob_re(pattern, False).fullmatch(text) is not None


def _expand_home(p: str) -> str:
    h = os.path.expanduser("~")
    for var in ("${HOME}", "$HOME"):
        if p.startswith(var):
            p = h + p[len(var):]
    return os.path.expanduser(p) if p.startswith("~") else p


def _real(p: str, cwd: str | None = None) -> str:
    p = _expand_home(p)
    if not os.path.isabs(p) and cwd:
        p = os.path.join(cwd, p)
    return os.path.realpath(p)


def _path_match(pattern: str, path: str) -> bool:
    """Gitignore-ish: `**` any depth, `*` one segment, `?` one character. The pattern's literal prefix is resolved
    through symlinks the same way the path is, so both sides compare as real locations."""
    pat = _expand_home(pattern.strip())
    if not pat.startswith("/"):
        pat = "**/" + pat
    first = re.search(r"[*?]", pat)
    if first:
        head, tail = pat[:first.start()], pat[first.start():]
        cut = head.rfind("/") + 1
        pat = (os.path.realpath(head[:cut] or "/").rstrip("/") + "/" + head[cut:] + tail) if head.startswith("/") else pat
    else:
        pat = os.path.realpath(pat)
    rx = _glob_re(pat, True)
    if rx.fullmatch(path):
        return True
    # A pattern with no wildcard names a folder or a file: everything under it counts too.
    return first is None and path.startswith(pat.rstrip("/") + "/")


# ---------------------------------------------------------------- shell parsing

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def normalize(cmd: str) -> str:
    """Undo the cheap ways of spelling the same command differently before anything is matched."""
    s = unicodedata.normalize("NFKC", cmd or "")
    s = _ANSI.sub("", s).replace("\x00", "")
    s = s.replace("\\\n", "")
    return s.replace("${IFS}", " ").replace("$IFS", " ")


@dataclass
class Seg:
    words: list[str]
    raw: str
    redirects: list[tuple[str, str]] = field(default_factory=list)  # (operator, target)


@dataclass
class Parsed:
    segments: list[Seg]
    nested: list[Seg]   # commands inside $(), backticks and shell -c strings: judged by deny / ask, never by allow
    opaque: bool
    text: str


def _match_close(s: str, i: int) -> int:
    """Index of the ')' closing the '(' just before s[i], or -1."""
    depth = 1
    quote = ""
    while i < len(s):
        c = s[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 1
            elif c == quote:
                quote = ""
        elif c in "'\"":
            quote = c
        elif c == "\\":
            i += 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _scan(cmd: str, subst_ok: bool = False) -> Parsed:
    """Conservative shell tokenizer. Anything it cannot be sure about sets `opaque`, which means ask.

    `subst_ok` (the syntax-tree path) lets $() through as a placeholder word, because the commands inside are
    judged on their own."""
    s = cmd
    segs: list[Seg] = []
    nested: list[Seg] = []
    opaque = False
    words: list[str] = []
    reds: list[tuple[str, str]] = []
    cur: list[str] = []
    started = False
    pending_red: str | None = None
    seg_start = 0
    last_op = ""  # the control operator that ended the previous segment
    i, n = 0, len(s)

    def end_word() -> None:
        nonlocal cur, started, pending_red
        if not started:
            return
        w = "".join(cur)
        cur, started = [], False
        if pending_red is not None:
            op, pending_red = pending_red, None
            if not (op.endswith("&") and (w.isdigit() or w == "-")):
                reds.append((op, w))
        else:
            words.append(w)

    def end_seg(i_end: int, op: str) -> None:
        nonlocal words, reds, seg_start, opaque, last_op
        end_word()
        raw = s[seg_start:i_end].strip()
        if words or reds:
            segs.append(Seg(words, raw, reds))
        elif op in ("&&", "||", "|", "|&") or last_op in ("&&", "||", "|", "|&"):
            opaque = True  # an operator with nothing on one side
        words, reds = [], []
        seg_start = i_end + len(op)
        last_op = op

    while i < n:
        c = s[i]
        if c == "\\":
            cur.append(s[i + 1] if i + 1 < n else "")
            started = True
            i += 2
            continue
        if c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                opaque = True
                break
            cur.append(s[i + 1:j])
            started = True
            i = j + 1
            continue
        if c == '"':
            j, buf = i + 1, []
            closed = False
            while j < n:
                d = s[j]
                if d == "\\" and j + 1 < n:
                    buf.append(s[j + 1] if s[j + 1] in '"\\$`' else "\\" + s[j + 1])
                    j += 2
                    continue
                if d == '"':
                    closed = True
                    break
                if d == "$" and s[j + 1:j + 2] == "(":
                    k = _match_close(s, j + 2)
                    if k < 0:
                        opaque = True
                        break
                    inner = _scan(s[j + 2:k], subst_ok)
                    nested += inner.segments + inner.nested
                    opaque = opaque or (inner.opaque or not subst_ok)
                    buf.append("$SUBST")
                    j = k + 1
                    continue
                if d == "`":
                    k = s.find("`", j + 1)
                    if k < 0:
                        opaque = True
                        break
                    inner = _scan(s[j + 1:k], subst_ok)
                    nested += inner.segments + inner.nested
                    opaque = opaque or (inner.opaque or not subst_ok)
                    buf.append("$SUBST")
                    j = k + 1
                    continue
                buf.append(d)
                j += 1
            if not closed:
                opaque = True
                break
            cur.append("".join(buf))
            started = True
            i = j + 1
            continue
        if c == "$":
            nx = s[i + 1:i + 2]
            if nx == "(":
                k = _match_close(s, i + 2)
                if k < 0:
                    opaque = True
                    break
                inner = _scan(s[i + 2:k], subst_ok)
                nested += inner.segments + inner.nested
                opaque = opaque or (inner.opaque or not subst_ok)
                cur.append("$SUBST")
                started = True
                i = k + 1
                continue
            if nx in ("'", '"'):  # $'..' escapes: not worth decoding
                opaque = True
            cur.append(c)
            started = True
            i += 1
            continue
        if c == "`":
            k = s.find("`", i + 1)
            if k < 0:
                opaque = True
                break
            inner = _scan(s[i + 1:k], subst_ok)
            nested += inner.segments + inner.nested
            opaque = opaque or (inner.opaque or not subst_ok)
            cur.append("$SUBST")
            started = True
            i = k + 1
            continue
        if c in "<>" and s[i + 1:i + 2] == "(":
            opaque = True
            i += 2
            continue
        if c == "<" and s[i + 1:i + 2] == "<" and s[i + 2:i + 3] != "<":
            opaque = True  # heredoc: its body is another program's input and not worth guessing at
            i += 2
            continue
        if c in "()":
            opaque = True
            i += 1
            continue
        if c == "{" and not started and s[i + 1:i + 2] in (" ", "\t", "\n"):
            opaque = True
            i += 1
            continue
        if c in " \t":
            end_word()
            i += 1
            continue
        if c == "#" and not started:
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "\n":
            end_seg(i, "\n")
            i += 1
            continue
        if c == ";":
            end_seg(i, ";")
            i += 1
            continue
        if c in "&|":
            two = s[i:i + 2]
            if c == "&" and s[i + 1:i + 2] == ">":  # &> file
                end_word()
                pending_red = "&>"
                i += 2 + (1 if s[i + 2:i + 3] == ">" else 0)
                continue
            if two in ("&&", "||", "|&"):
                end_seg(i, two)
                i += 2
                continue
            end_seg(i, c)
            i += 1
            continue
        if c in "<>":
            # an fd number glued to the operator (2>) belongs to it
            if started and "".join(cur).isdigit():
                cur, started = [], False
            else:
                end_word()
            op = c
            j = i + 1
            if s[j:j + 1] == c:
                op += c
                j += 1
            elif s[j:j + 1] == "&":
                op += "&"
                j += 1
            elif c == ">" and s[j:j + 1] == "|":
                j += 1
            pending_red = op
            i = j
            continue
        cur.append(c)
        started = True
        i += 1
    if not opaque:
        end_seg(n, "")  # a trailing && / || / | with nothing after it is an unfinished command: opaque
    return Parsed(segs, nested, opaque, s)


_ts_parser: Any = None


def _ts() -> Any:
    """The tree-sitter bash parser when the binding is installed, else None."""
    global _ts_parser
    if _ts_parser is None:
        try:
            import tree_sitter_bash as tsb  # type: ignore[import-not-found]
            from tree_sitter import Language, Parser  # type: ignore[import-not-found]
            _ts_parser = Parser(Language(tsb.language()))
        except Exception:  # noqa: BLE001 - absent or incompatible: the shlex-style scanner carries on
            _ts_parser = False
    return _ts_parser or None


def _parse_ts(cmd: str) -> Parsed | None:
    parser = _ts()
    if parser is None:
        return None
    try:
        data = cmd.encode()
        root = parser.parse(data).root_node
        if root.has_error:
            return None
        segs: list[Seg] = []
        opaque = False
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type in ("heredoc_redirect", "heredoc_body", "process_substitution"):
                opaque = True
            if node.type == "command":
                sub = _scan(data[node.start_byte:node.end_byte].decode(errors="replace"), subst_ok=True)
                segs += sub.segments
            elif node.type == "file_redirect":
                dest = node.child_by_field_name("destination")
                if dest is not None:
                    op = "<" if data[node.start_byte:node.start_byte + 1] == b"<" else ">"
                    segs.append(Seg([], "", [(op, data[dest.start_byte:dest.end_byte].decode(errors="replace").strip("'\""))]))
            stack.extend(reversed(node.children))
        return Parsed(segs, [], opaque, cmd)
    except Exception:  # noqa: BLE001
        return None


def split_command(cmd: str) -> Parsed:
    """A command line as its subcommands. Nested commands come from the syntax tree when its binding is installed."""
    text = normalize(cmd)
    return _parse_ts(text) or _scan(text)


def strip_wrappers(tokens: list[str], aggressive: bool = False) -> list[str]:
    """Drop env prefixes and the wrappers that only run what follows. `aggressive` is for deny / ask matching."""
    t = list(tokens)
    env = ENV_ASSIGN if aggressive else SAFE_ENV
    for _ in range(12):
        while t and env.match(t[0]):
            t.pop(0)
        if not t:
            break
        name = os.path.basename(t[0])
        if name == "timeout":
            j = 1
            while j < len(t) and t[j].startswith("-"):
                j += 2 if t[j] in ("-k", "-s", "--kill-after", "--signal") else 1
            j += 1  # the duration
            t = t[j:]
        elif name == "time":
            t = t[1:]
            while t and t[0].startswith("-"):
                t = t[1:]
        elif name == "nice":
            t = t[1:]
            if t and t[0] == "-n":
                t = t[2:]
            elif t and re.fullmatch(r"-\d+", t[0]):
                t = t[1:]
        elif name in ("nohup", "builtin"):
            t = t[1:]
        elif name == "command":
            if len(t) > 1 and t[1] in ("-v", "-V"):
                break
            t = t[1:]
        elif name == "stdbuf":
            t = t[1:]
            while t and t[0].startswith("-"):
                t = t[1:]
        elif name == "xargs":
            if len(t) > 1 and t[1].startswith("-"):
                if not aggressive:
                    break
                t = t[1:]
                while t and t[0].startswith("-"):
                    f = t.pop(0)
                    if f in XARGS_ARG_FLAGS and t:
                        t.pop(0)
            else:
                t = t[1:]
        elif aggressive and name in ("sudo", "doas"):
            t = t[1:]
            while t and t[0].startswith("-"):
                f = t.pop(0)
                if f in ("-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T") and t:
                    t.pop(0)
        elif aggressive and name == "env":
            t = t[1:]
            while t and (t[0].startswith("-") or ENV_ASSIGN.match(t[0])):
                t.pop(0)
        elif aggressive and name in ("exec", "setsid", "caffeinate"):
            t = t[1:]
        else:
            break
    return t


def _shell_c(tokens: list[str]) -> str | None:
    """The program string of `sh -c '...'`, which deny / ask rules read as commands of their own."""
    if not tokens or os.path.basename(tokens[0]) not in SHELLS:
        return None
    for j, w in enumerate(tokens[1:], 1):
        if w.startswith("-") and not w.startswith("--") and "c" in w:
            return tokens[j + 1] if j + 1 < len(tokens) else None
    return None


# ---------------------------------------------------------------- hardline

_FORK_COLON = re.compile(r":\(\)\{:\|:&?\}")
_FORK_BODY = re.compile(r"(\w+)\|\1&\}")


def _fork_bomb(text: str) -> bool:
    r"""`:(){:|:&}` or `f(){f|f&}` (whitespace already removed). Anchored on each `(){` instead of searching for
    `(\w+)\(\)\{\1...`, which tries every start of a long word and takes quadratic time on a long command."""
    if _FORK_COLON.search(text):
        return True
    i = text.find("(){")
    while i != -1:
        m = _FORK_BODY.match(text, i + 3)
        if m and text.endswith(m.group(1), 0, i):
            return True
        i = text.find("(){", i + 1)
    return False
SYSTEM_DIRS = {"/", "/System", "/usr", "/bin", "/sbin", "/etc", "/var", "/Library", "/Applications", "/private",
               "/opt", "/Users", "/dev", "/cores", "/Volumes"}


def _hardline_tokens(tokens: list[str], redirects: list[tuple[str, str]]) -> str | None:
    h = os.path.expanduser("~")
    for _, target in redirects:
        if target.startswith(("/dev/disk", "/dev/rdisk")):
            return "writing to a disk device"
    if not tokens:
        return None
    name = os.path.basename(tokens[0])
    args = tokens[1:]
    if name == "rm":
        recursive = any((a.startswith("-") and not a.startswith("--") and ("r" in a or "R" in a)) or a == "--recursive" for a in args)
        if "--no-preserve-root" in args:
            return "rm with --no-preserve-root"
        if recursive:
            for a in args:
                if a.startswith("-"):
                    continue
                p = _expand_home(a)
                for suffix in ("/*", "/.*"):
                    if p.endswith(suffix) and len(p) > len(suffix) - 1:
                        p = p[:-len(suffix)] or "/"
                p = p.rstrip("/") or "/"
                if p in SYSTEM_DIRS or p == h:
                    return f"recursive delete of {a}"
    if name.startswith("mkfs") or name == "newfs" or name.startswith("newfs_"):
        return "formatting a filesystem"
    if name == "dd" and any(a.startswith(("of=/dev/disk", "of=/dev/rdisk", "of=/dev/sd")) for a in args):
        return "dd to a disk device"
    if name in ("tee", "cp", "mv", "cat") and any(a.startswith(("/dev/disk", "/dev/rdisk")) for a in args):
        return "writing to a disk device"
    if name == "kill" and args and args[-1] == "-1":
        return "kill -1 (every process)"
    if name in ("shutdown", "reboot", "halt", "poweroff", "killall5"):
        return f"{name}"
    if name == "diskutil" and args and re.match(r"(erase|secureErase|zeroDisk|randomDisk|reformat)", args[0], re.I):
        return "diskutil erase"
    return None


def hardline(cmd: str, parsed: Parsed | None = None) -> str | None:
    """Why this command is never allowed to run, or None. Checked before everything; no rule or card lifts it."""
    text = normalize(cmd)
    if _fork_bomb(re.sub(r"\s+", "", text)):
        return "a fork bomb"
    p = parsed or split_command(cmd)
    for seg in p.segments + p.nested:
        for tokens in (strip_wrappers(seg.words, True), strip_wrappers(seg.words, False)):
            why = _hardline_tokens(tokens, seg.redirects)
            if why:
                return why
            inner = _shell_c(tokens)
            if inner is not None:
                why = hardline(inner)
                if why:
                    return why
    return None


# ---------------------------------------------------------------- subjects

@dataclass(frozen=True)
class Subject:
    kind: str   # Bash | Read | Edit | Agent | external_directory | <ToolName>
    value: str | None


def _first(args: dict[str, Any], keys: Iterable[str]) -> str | None:
    for k in keys:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return None


def subject_for(tool: str, args: dict[str, Any]) -> list[Subject]:
    """What a rule is matched against for this call, per the shared contract. A tool that is not built yet is
    still mapped by name, so the rules written for it work the day it lands."""
    a = args if isinstance(args, dict) else {}
    if tool == "shell_run":
        return [Subject("Bash", str(a.get("command") or ""))]
    if tool in READ_TOOLS:
        p = _first(a, PATH_KEYS)
        return [Subject("Read", p)] if p else [Subject("Read", None)]
    if tool in EDIT_TOOLS:
        out = []
        dest = _first(a, DEST_KEYS) if tool in ("fs_copy", "move_local_file") else None
        p = _first(a, PATH_KEYS)
        if tool == "fs_copy":
            src = _first(a, SRC_KEYS) or p
            if src:
                out.append(Subject("Read", src))
        elif p:
            out.append(Subject("Edit", p))
        if dest:
            out.append(Subject("Edit", dest))
        return out or [Subject("Edit", None)]
    if tool in MAIL_TOOLS:  # one subject per address, the same ones the read-back compares
        raw = [x for k in ("to", "cc", "bcc") for x in ([a[k]] if isinstance(a.get(k), str) else a.get(k) or []) if isinstance(x, str)]
        addrs = list(dict.fromkeys(ad.strip().lower() for _, ad in getaddresses(raw) if ad.strip()))
        return [Subject(tool, ad) for ad in addrs] or [Subject(tool, None)]
    if tool in CALENDAR_TOOLS:
        ch = a.get("changes") if tool == "calendar_propose" else [a]
        cals = list(dict.fromkeys((c.get("calendar_id") or "primary") if isinstance(c, dict) else "primary"
                                  for c in ch)) if isinstance(ch, list) and ch else []
        return [Subject(tool, str(c)) for c in cals] or [Subject(tool, None)]
    if tool == "agent_spawn":
        return [Subject("Agent", _first(a, ("agent", "type", "role", "agent_type")) or None)]
    return [Subject(tool, None)]


def _matches(rule: Rule, sub: Subject, tool: str, cwd: str | None) -> bool:
    if rule.tool != sub.kind and not (rule.tool == tool and rule.pattern is None):
        return False
    if rule.pattern is None:
        return True
    if sub.value is None:
        return False
    if sub.kind in ("Read", "Edit"):
        return _path_match(rule.pattern, _real(sub.value, cwd))
    if sub.kind == "external_directory":  # a folder: `dir/**` covers the folder itself too
        p = _real(sub.value, cwd)
        return _path_match(rule.pattern, p) or _path_match(rule.pattern, p.rstrip("/") + "/")
    if tool in MAIL_TOOLS:  # addresses are case-insensitive
        return _cmd_match(rule.pattern.lower(), sub.value)
    return _cmd_match(rule.pattern, sub.value)


def _decide(rules: RuleSet, subs: list[Subject], tool: str, cwd: str | None) -> tuple[str | None, str | None]:
    """deny, then ask, then allow over one group of subjects; returns (verdict, the rule that decided)."""
    for sub in subs:
        for r in rules.deny:
            if _matches(r, sub, tool, cwd):
                return "deny", r.text
    for sub in subs:
        for r in rules.ask:
            if _matches(r, sub, tool, cwd):
                return "ask", r.text
    allowed = []
    for sub in subs:
        hit = next((r.text for r in rules.allow if _matches(r, sub, tool, cwd)), None)
        allowed.append(hit)
    if subs and all(allowed):
        return "allow", allowed[0]
    return None, None


# ---------------------------------------------------------------- shell evaluation

def _readonly(tokens: list[str], redirects: list[tuple[str, str]]) -> bool:
    if not tokens:
        return False
    if any(op.startswith((">", "&>")) and t not in EXEMPT_PATHS for op, t in redirects):
        return False
    if any(SENSITIVE.search(w) for w in tokens[1:]):
        return False
    name = tokens[0]
    if name == "git":
        if len(tokens) < 2 or tokens[1] not in GIT_READONLY:
            return False
        if tokens[1] == "branch":
            return all(a in GIT_BRANCH_FLAGS for a in tokens[2:])
        return not any(a.startswith("--output") or a == "--ext-diff" for a in tokens[2:])
    if name not in READONLY:
        return False
    if name == "find":
        return not any(a in FIND_WRITERS for a in tokens[1:])
    if name in ("rg", "grep"):
        return not any(a.startswith("--pre") for a in tokens[1:])
    return True


def _roots(roots: Iterable[str]) -> list[str]:
    return [os.path.realpath(_expand_home(r)) for r in roots if r]


def _guarded_paths(seg: Seg, tokens: list[str], cwd: str | None) -> list[str]:
    """Paths this subcommand names that are a credential store or Grain's own data folder or app (mac.sensitive_reason /
    mac.protected_reason), judged as spelled and as resolved: the file itself, or the folder for a glob."""
    from . import mac
    out: list[str] = []
    targets: list[str] = [t for op, t in seg.redirects if t not in EXEMPT_PATHS]
    name = os.path.basename(tokens[0]) if tokens else ""
    if name in PATH_COMMANDS:
        pos = [w for w in tokens[1:] if not w.startswith("-")]
        if name in LEADING_ARG_COMMANDS and pos:
            pos = pos[1:]
        if name == "cd" and not pos:
            pos = ["~"]
        targets += [w for w in pos if w != "-"]
    for t in targets:
        if "$" in t and not t.startswith(("$HOME", "${HOME}")):
            continue  # an unresolved variable cannot be judged
        if re.search(r"[*?\[]", t):
            t = os.path.dirname(re.split(r"[*?\[]", t)[0] + "x") or "."
        spelled = _expand_home(t)
        if not os.path.isabs(spelled) and cwd:
            spelled = os.path.join(cwd, spelled)
        p = _real(t, cwd)
        if (mac.sensitive_reason(os.path.normpath(spelled), p) or mac.protected_reason(spelled, p)) and p not in out:
            out.append(p)
    return out


def _canon(tokens: list[str]) -> str:
    return " ".join(tokens)


def arity_prefix(tokens: list[str]) -> list[str]:
    """The part of a command a reusable rule should keep: `git commit -m x` -> `git commit`."""
    if not tokens:
        return []
    name = tokens[0]
    n = ARITY.get(name, 1)
    if n == 2 and len(tokens) > 1 and not tokens[1].startswith("-"):
        if (name, tokens[1]) in ARITY_THREE and len(tokens) > 2 and not tokens[2].startswith("-"):
            return tokens[:3]
        return tokens[:2]
    return tokens[:1]


@dataclass
class Verdict:
    action: str | None = None            # deny | ask | allow | None
    refusal: str | None = None           # why a deny was a deny
    hardline: bool = False
    rule: str | None = None              # the rule that decided, when one did
    kind: str | None = None              # rule | opaque | external_directory | tool
    pending: list[str] = field(default_factory=list)      # session keys of what an approval would cover
    suggestions: list[str] = field(default_factory=list)  # rules the card can offer to save
    subjects: list[str] = field(default_factory=list)
    external: list[str] = field(default_factory=list)


def _evaluate_bash(tool: str, cmd: str, rules: RuleSet, cwd: str | None) -> Verdict:
    v = Verdict(subjects=[f"Bash({cmd})"])
    why = hardline(cmd)
    if why:
        v.action, v.hardline, v.refusal = "deny", True, f"never allowed ({why})"
        return v
    parsed = split_command(cmd)
    # deny / ask see every command there is, including those inside $() and sh -c strings.
    extra: list[Seg] = list(parsed.nested)
    for seg in parsed.segments + parsed.nested:
        inner = _shell_c(strip_wrappers(seg.words, True))
        if inner is not None:
            sub = split_command(inner)
            extra += sub.segments + sub.nested
            parsed.opaque = parsed.opaque or sub.opaque
    judged = parsed.segments + extra

    def variants(seg: Seg) -> list[Subject]:
        agg, cons = strip_wrappers(seg.words, True), strip_wrappers(seg.words, False)
        texts = {_canon(agg), _canon(cons), _canon(seg.words), seg.raw}
        return [Subject("Bash", t) for t in texts if t]

    for seg in judged:
        for sub in variants(seg):
            for r in rules.deny:
                if _matches(r, sub, tool, cwd):
                    v.action, v.rule = "deny", r.text
                    v.refusal = f"blocked by your permission rule {r.text}"
                    return v
    asked: list[str] = []
    for seg in judged:
        for sub in variants(seg):
            hit = next((r for r in rules.ask if _matches(r, sub, tool, cwd)), None)
            if hit:
                asked.append(f"Bash({_canon(strip_wrappers(seg.words, True))})")
                v.rule = v.rule or hit.text
                break
    # A credential store or Grain's own data is judged by the agg tokens, wrappers and all.
    outside: list[str] = []
    for seg in judged:
        for d in _guarded_paths(seg, strip_wrappers(seg.words, True), cwd):
            for r in rules.deny:
                if _matches(r, Subject("external_directory", d), tool, cwd):
                    v.action, v.rule = "deny", r.text
                    v.refusal = f"blocked by your permission rule {r.text}"
                    return v
            if not any(_matches(r, Subject("external_directory", d), tool, cwd) for r in rules.allow) and d not in outside:
                outside.append(d)
    v.external = outside
    # allow: every subcommand of the line itself needs a verdict.
    unallowed: list[str] = []
    for seg in parsed.segments:
        agg = strip_wrappers(seg.words, True)
        cons = strip_wrappers(seg.words, False)
        if not seg.words and seg.redirects:
            continue  # a bare redirect rides with the command it belongs to
        ok = _readonly(cons, seg.redirects) or any(
            _matches(r, Subject("Bash", _canon(cons)), tool, cwd) for r in rules.allow)
        if not ok:
            unallowed.append(f"Bash({_canon(agg)})")
    if asked or parsed.opaque or outside:
        v.action = "ask"
        v.kind = "rule" if asked else ("opaque" if parsed.opaque else "external_directory")
        if parsed.opaque:
            v.pending = [f"Bash({normalize(cmd).strip()})"]
        else:
            v.pending = list(dict.fromkeys(asked + unallowed)) + [f"external_directory({d})" for d in outside]
        v.suggestions = _suggest_bash(parsed, rules, outside) if not parsed.opaque else []
        return v
    if unallowed:
        v.action = None
        v.pending = list(dict.fromkeys(unallowed))
        v.suggestions = _suggest_bash(parsed, rules, [])
        return v
    v.action = "allow"
    return v


def _suggest_bash(parsed: Parsed, rules: RuleSet, outside: list[str]) -> list[str]:
    out: list[str] = []
    for seg in parsed.segments:
        cons = strip_wrappers(seg.words, False)
        if not cons or _readonly(cons, seg.redirects):
            continue
        pre = arity_prefix(cons)
        rule = f"Bash({' '.join(pre)} *)" if len(pre) < len(cons) else f"Bash({' '.join(pre)})"
        if rule not in out:
            out.append(rule)
    for d in outside:
        r = f"external_directory({d}/**)"
        if r not in out:
            out.append(r)
    return out[:MAX_SUGGESTIONS]


def evaluate(tool: str, args: dict[str, Any], rules: RuleSet | dict[str, Any] | None, *,
             roots: Iterable[str] = (), cwd: str | None = None) -> Verdict:
    """The rule verdict for one call: deny > ask > allow, None when no rule has an opinion. `roots` only names where a
    relative path in a shell command starts (the first entry, the desk workspace); it limits nothing."""
    rs = rules if isinstance(rules, RuleSet) else load_rules(rules)
    rl = _roots(roots)
    if cwd is None and rl:
        cwd = rl[0]
    args = args if isinstance(args, dict) else {}
    if tool == "shell_run":
        c = args.get("cwd")
        return _evaluate_bash(tool, str(args.get("command") or ""), rs, _real(c) if isinstance(c, str) and c else cwd)
    subs = subject_for(tool, args)
    v = Verdict(subjects=[f"{s.kind}({s.value})" if s.value else s.kind for s in subs])
    verdict, rule = _decide(rs, subs, tool, cwd)
    v.action, v.rule = verdict, rule
    if verdict == "deny":
        v.refusal = f"blocked by your permission rule {rule}"
    elif verdict == "ask":
        v.kind = "rule"
        v.pending = list(v.subjects)
    elif verdict is None:
        v.pending = [tool] if all(s.kind == tool and s.value is None for s in subs) else list(v.subjects)
    # Rules written for the plain tool name apply to every kind of subject it has.
    if verdict is None and not rs.empty():
        plain, plain_rule = _decide(rs, [Subject(tool, None)], tool, cwd)
        if plain in ("deny", "ask"):
            v.action, v.rule = plain, plain_rule
            if plain == "deny":
                v.refusal = f"blocked by your permission rule {plain_rule}"
            else:
                v.kind, v.pending = "rule", [tool]
    v.suggestions = _suggest_paths(subs) if v.action != "allow" else []
    return v


def _suggest_paths(subs: list[Subject]) -> list[str]:
    out = []
    for s in subs:
        if s.kind in ("Read", "Edit") and s.value:
            p = _real(s.value)
            r = f"{s.kind}({os.path.dirname(p) or '/'}/**)"
            if r not in out:
                out.append(r)
        elif s.kind in MAIL_TOOLS | CALENDAR_TOOLS and s.value:
            out.append(f"{s.kind}({s.value})")
        elif s.kind == "Agent" and s.value:
            out.append(f"Agent({s.value})")
    return out[:MAX_SUGGESTIONS]


# ---------------------------------------------------------------- the call-time decision

@dataclass
class Resolution:
    mode: str
    forced: bool
    refusal: str | None = None
    hardline: bool = False
    kind: str | None = None       # what the card is about: rule | opaque | external_directory | doom_loop | None
    display: str | None = None
    keys: list[str] = field(default_factory=list)          # what "allow for this session" would cover
    suggestions: list[str] = field(default_factory=list)   # rules the card offers to save
    rule: str | None = None

    def card(self) -> dict[str, Any] | None:
        """What the approval card shows beyond the tool name; None when there is nothing to add."""
        if self.mode != "ask" or not (self.suggestions or self.kind or self.keys):
            return None
        return {"kind": self.kind, "subject": self.display, "rule": self.rule, "suggestions": self.suggestions,
                "session": bool(self.keys) and not self.forced}


class SessionGrants:
    """'Allow for this chat session': in memory, per conversation, gone on restart."""

    def __init__(self) -> None:
        self._g: dict[str, set[str]] = {}

    def add(self, conv: str, keys: Iterable[str]) -> None:
        self._g.setdefault(conv, set()).update(keys)

    def covers(self, conv: str, keys: Iterable[str]) -> bool:
        ks = list(keys)
        return bool(ks) and set(ks) <= self._g.get(conv, set())

    def clear(self, conv: str | None = None) -> None:
        self._g.pop(conv, None) if conv else self._g.clear()

    def remove(self, conv: str, key: str) -> bool:
        """Revoke one key; the chat's other grants stand. False when it was not granted."""
        ks = self._g.get(conv)
        if not ks or key not in ks:
            return False
        ks.discard(key)
        if not ks:
            del self._g[conv]
        return True

    def list(self) -> dict[str, list[str]]:
        return {c: sorted(ks) for c, ks in self._g.items() if ks}


SESSION = SessionGrants()


def resolve(tool: str, args: dict[str, Any], mode: str, forced: bool, *, rules: RuleSet | dict[str, Any] | None,
            roots: Iterable[str] = (), cwd: str | None = None, conv: str | None = None,
            doom: bool = False) -> Resolution:
    """Fold the rules, the session grants and the doom-loop check into this call's mode.

    A deny (or the hardline list) refuses. A forced approval is never downgraded by a rule or a session grant.
    ask turns on a card, allow turns a card off, and no opinion leaves the tool's own mode alone."""
    res = Resolution(mode, forced)
    if mode == "off":
        return res
    v = evaluate(tool, args, rules, roots=roots, cwd=cwd)
    if v.action == "deny":
        res.refusal, res.hardline, res.rule = v.refusal, v.hardline, v.rule
        return res
    if doom:
        res.mode, res.forced, res.kind, res.display = "ask", True, "doom_loop", f"doom_loop({tool})"
        return res
    res.rule, res.suggestions, res.keys = v.rule, v.suggestions, v.pending
    res.display = v.pending[0] if v.pending else None
    covered = bool(conv) and SESSION.covers(conv or "", v.pending)
    if v.action == "ask":
        res.kind = v.kind
        res.mode = "on" if (covered and not forced) else "ask"
    elif v.action == "allow":
        if mode == "ask" and not forced:
            res.mode = "on"
    elif mode == "ask" and not forced and covered:
        res.mode = "on"
    return res


def mcp_denied(slug: str, rules: RuleSet | dict[str, Any] | None) -> str | None:
    """Refusal text when a deny rule names this MCP slug (`mcp__srv__tool`) or its whole server (`mcp__srv`).
    Only denies apply: a grant is the one thing that turns an MCP tool on, so allow/ask rules are ignored here."""
    rs = rules if isinstance(rules, RuleSet) else load_rules(rules)
    for r in rs.deny:
        if r.pattern is None and (r.tool.lower() == slug.lower() or slug.lower().startswith(r.tool.lower() + "__")):
            return f"blocked by your permission rule {r.text}"
    return None


def skip_permissions_on(conv_settings: dict[str, Any] | None, cfg: dict[str, Any] | None) -> bool:
    """Whether approval cards are skipped: only the global Allow all mode does (a chat's own skipPermissions is legacy)."""
    return permissions.get(cfg or {}, "permissionMode") == "allow_all"


def lift_permission_ask(name: str, mode: str, *, skip: bool, forced: bool = False, danger: str = "",
                        fenced: bool = False) -> str:
    """Turn a plain ask into a run. A deny is not an ask (the caller keeps the refusal). Off stays off.

    Stays a card: a plan or desk question (the user deciding, not granting a tool), a forced ask (taint,
    doom loop, desk ask-as-you-go), an ask rule or an outside-folder write (`fenced`), an external or
    schedules tool (except coding_session_start/send, which skip lifts), and a shell command no read-only list
    or allow rule already cleared (a shell_run that is still `ask` here was not cleared).
    """
    if (skip and mode == "ask" and name not in STILL_ASK and not forced and not fenced
            and (danger not in ("external", "schedules") or name in ("coding_session_start", "coding_session_send"))
            and name not in ("shell_run", "opencode_run")):
        return "on"
    return mode


class DenialStreak:
    """Counts consecutive refused calls so the next result can tell the model to stop varying them."""

    def __init__(self) -> None:
        self.n = 0

    def note(self) -> str | None:
        return HARD_STOP if self.n >= DENIAL_LIMIT else None

    def record(self, denied: bool) -> None:
        self.n = self.n + 1 if denied else 0


def validate_saved_rules(tool: str, args: dict[str, Any], texts: list[str]) -> list[str]:
    """Rules a card may save: well formed, for a subject this call actually has, never a blanket pattern."""
    kinds = {s.kind for s in subject_for(tool, args)} | {"external_directory", tool}
    out: list[str] = []
    for t in texts[:MAX_SUGGESTIONS]:
        r = parse_rule(t)  # ValueError -> the route turns it into a 400
        if r.tool not in kinds:
            raise ValueError(f"{r.text} is not a rule for this call")
        if r.pattern is None or r.pattern.strip("* ") == "":
            raise ValueError(f"{r.text} would allow everything; narrow it")
        if r.tool in MAIL_TOOLS | CALENDAR_TOOLS and not any(_matches(r, s, tool, None) for s in subject_for(tool, args)):
            raise ValueError(f"{r.text} does not match this call's recipient or calendar")
        if r.text not in out:
            out.append(r.text)
    return out
