"""The agent's own interactive browser: browser_* tools over the desktop app's bridge.

The browser itself lives in the Electron main process (its own cookies, separate from the user's). This module is the
policy layer in front of it. It decides where the agent may navigate, which actions need the user's yes first, and
what text may reach the model, and it never trusts a page: every result is untrusted content (`taints=True`).

Pages are read as text snapshots whose elements carry refs (`e3 button "Sign in"`); a ref is good until the next
snapshot or navigation. Consequential actions (submitting a form, typing into a password or payment field, a click
that starts a download) are previewed first and raise a forced approval card from inside the call. Sign-ins and
CAPTCHAs are never done by the agent: `browser_manage(action="handoff")` shows the window to the user and waits.
"""
from __future__ import annotations

import base64
import os
import tempfile
import urllib.parse
import time
from pathlib import Path
from typing import Any

from . import fsx, mac, permissions, redact, tools
from .tools import ToolSpec, UrlBlocked, _obj, tool_error
from .workspace import WorkspaceError

SNAPSHOT_CAP = 12_000  # what the model may be handed, whatever main sent
# Credential shapes only; the entropy/card rules would eat ids and prices, which are most of what a page shows.
BROWSER_REDACT = ("private_key", "aws_key", "github_pat", "google_api", "slack_webhook", "jwt", "token")
MANAGE_ACTIONS = ("back", "forward", "reload", "tabs", "switch_tab", "close_tab", "wait", "screenshot", "dialog",
                  "upload", "handoff", "close")
UNTYPED = "this address was not typed by you or returned by a search"
DOWNLOADED = "downloaded:"  # the prefix of the note the desktop app adds for a finished download
DECLINED = ("the user did not allow this. Ask them with desk_ask, or hand the page over with "
            "browser_manage(action='handoff', reason=...) and let them do it")

# error code -> what to do next
FORWARD = {
    "stale_ref": "take a new snapshot with browser_snapshot; refs change after every action and every navigation",
    "dialog_open": "answer it with browser_manage(action='dialog', accept=true|false) first",
    "busy": "wait a moment (browser_manage(action='wait', ms=1500)) and try again",
    "timeout": "the page may still be loading: browser_snapshot to see where it got to, or try again",
    "blocked_host": "that address cannot be opened from here; tell the user, or use a different page",
    "too_many_tabs": "close one with browser_manage(action='close_tab', tab=N) (see action='tabs') or reuse a tab",
    "too_many_sessions": "ask the user to close another desk's browser, or finish with what you already have",
    "not_interactable": "scroll it into view with browser_scroll or pick another element from a fresh browser_snapshot",
    "no_session": "open a page first with browser_open",
    "bad_request": "check the arguments against the tool's examples",
    "bridge": "the desktop app's browser is not reachable; tell the user and carry on without it",
}


def session_of(ctx: dict[str, Any]) -> str:
    """Never an argument: a desk owns one browser session, a plain chat owns another."""
    if ctx.get("desk_id"):
        return f"desk:{ctx['desk_id']}"
    return f"conv:{ctx.get('conversation_id') or 'none'}"


def _clip(text: str) -> tuple[str, bool]:
    text = redact.scrub(text, BROWSER_REDACT, secret_assign=False)
    return (text[:SNAPSHOT_CAP], True) if len(text) > SNAPSHOT_CAP else (text, False)


def _shown(value: Any) -> Any:
    """Strings in a page result the model reads. Numbers and flags stay."""
    if isinstance(value, str):
        return redact.scrub_command_output(value)
    if isinstance(value, list):
        return [_shown(item) for item in value]
    if isinstance(value, dict):
        return {key: _shown(item) for key, item in value.items()}
    return value


def _fail(name: str, res: dict[str, Any]) -> dict[str, Any]:
    code = str(res.get("code") or "")
    msg = redact.scrub_command_output(str(res.get("error") or "the browser call failed"))[:400]
    return tool_error(f"{name}: {msg}", alternative=FORWARD.get(code))


def register(tb: Any) -> None:
    """Register this module's tools on the Toolbox."""
    R = tb.specs.__setitem__

    def settings() -> dict[str, Any]:
        return tb.settings()

    def available() -> bool:
        return bool(permissions.get(settings(), "browserEnabled")) and mac.page_bridge.has("browser")

    def desk_root(ctx: dict[str, Any]) -> Path | None:
        ws, did = getattr(tb, "workspace", None), ctx.get("desk_id")
        if ws is None or not did:
            return None
        try:
            return ws.ensure(str(did))
        except Exception:  # noqa: BLE001 - no workspace means "no desk files", not a crash
            return None

    def chat_root(ctx: dict[str, Any]) -> Path | None:
        """A plain chat's files (tools.Toolbox.files_for): browser downloads land under its outputs/downloads/."""
        box = tb.files_for(ctx) if not ctx.get("desk_id") else None
        if box is None:
            return None
        try:
            return box[0].ensure(box[1])
        except Exception:  # noqa: BLE001 - no outbox means downloads stay refused, not a crash
            return None

    def shaped(root: Path | None, res: dict[str, Any], chat: Any = None) -> dict[str, Any]:
        """What the model sees: the page state, the snapshot verbatim (it carries its own delimiters), notes."""
        snap, cut = _clip(str(res.get("snapshot") or ""))
        notes = [str(n) for n in (res.get("notes") or [])][:20]
        if cut:
            notes.append(f"snapshot cut at {SNAPSHOT_CAP} characters; use browser_snapshot(query=...) to look for something specific")
        out: dict[str, Any] = {"url": res.get("url") or "", "title": res.get("title") or "", "tab": res.get("tab"),
                               "tabs": res.get("tabs"), "snapshot": snap, "notes": notes}
        if res.get("tabList") is not None:
            out["tab_list"] = res["tabList"]
        # The desktop app reports a finished download as a note, "downloaded: <absolute path>". The model gets the path
        # relative to the desk workspace, which is what the desk file tools take. In a plain chat (`chat` is the
        # outbox's (workspace, conversation id)) it is relative to the chat's files, and listed as an output for the card.
        base = root if root is not None else chat[0].desk_root(chat[1]) if chat else None
        files, outputs = [], []
        for i, n in enumerate(notes):
            if not n.startswith(DOWNLOADED):
                continue
            raw = n[len(DOWNLOADED):].strip()
            try:
                rel = os.path.relpath(raw, base) if base and os.path.isabs(raw) else raw
            except ValueError:
                rel = raw
            files.append(rel)
            notes[i] = DOWNLOADED + " " + rel
            if chat:
                try:
                    p = chat[0].resolve_in(chat[1], rel)
                    if p.is_file():
                        outputs.append(chat[0].output_entry(chat[1], p))
                except (WorkspaceError, OSError):
                    pass
        if outputs:
            out = {"outputs": outputs, **out}
        if files:
            out["downloaded"] = files
        return _shown(out)

    async def call(name: str, route: str, payload: dict[str, Any], ctx: dict[str, Any], timeout: float = 45.0) -> dict[str, Any]:
        res = await mac.page_bridge.browser(route, {"session": session_of(ctx), **payload}, timeout)
        if not res.get("ok"):
            return _fail(name, res)
        return shaped(desk_root(ctx), res, None if ctx.get("desk_id") else tb.files_for(ctx))

    async def ask(ctx: dict[str, Any], args: dict[str, Any]) -> bool:
        approve = ctx.get("approve")
        if approve is None:  # subagents, workflows, tests: nobody to ask
            return False
        try:
            return bool(await approve("browser", args, True))
        except Exception:  # noqa: BLE001 - an approval that cannot be raised is a no
            return False

    # ---------------- navigation ----------------
    def host_allowed(url: str) -> bool:
        extra = {h for h in (str(x).strip().lower().lstrip(".") for x in (permissions.get(settings(), "browserAllowlist") or ())) if h}
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        return any(host == e or host.endswith("." + e) for e in extra)

    async def browser_open(ctx: dict[str, Any], url: str, new_tab: bool = False) -> Any:
        cfg = settings()
        merged = {**cfg, "fetchAllowlist": [*(permissions.get(cfg, "fetchAllowlist") or []), *(permissions.get(cfg, "browserAllowlist") or [])]}
        try:
            cur, host = tools._check_url(url, {**ctx, "tainted": False}, merged)  # SSRF and scheme rules, no taint rule
            await tools._resolve(host)
        except UrlBlocked as e:
            return tool_error(redact.scrub_command_output(
                f"browser_open refused {url}: {str(e).replace('fetch_url', 'browser_open')}"), field="url",
                              alternative=e.alternative or "fetch_url for a plain read, or ask the user for a different link")
        try:
            tools._check_url(url, ctx, merged)  # now the taint rule
        except UrlBlocked as e:
            # A tainted run may not navigate to an address the model composed. Unlike fetch_url, ask instead of refusing:
            # the user can see the address on the card.
            if not await ask(ctx, {"action": "open", "url": cur, "why": UNTYPED}):
                return tool_error(redact.scrub_command_output(
                    f"browser_open refused {url}: {str(e).replace('fetch_url', 'browser_open')}"), field="url",
                                  alternative=e.alternative or tools.TAINTED_HINT)
        root = desk_root(ctx)
        payload: dict[str, Any] = {"url": cur, "newTab": bool(new_tab), "timeoutMs": 30000,
                                   "maxTabs": int(cfg.get("browserMaxTabs") or 4), "idleSeconds": int(cfg.get("browserIdleSeconds") or 300)}
        chat = chat_root(ctx) if root is None else None
        dl = root / "work" / "downloads" if root is not None else chat / "outputs" / "downloads" if chat is not None else None
        if dl is not None:  # a download still needs the user's yes on its card (guarded); this is only where it lands
            dl.mkdir(parents=True, exist_ok=True)
            payload["downloadDir"] = str(dl)
        return await call("browser_open", "open", payload, ctx, 50)

    R("browser_open", ToolSpec("browser_open",
        "Open a web page in your own browser (a real page with JavaScript; its own cookies, separate from the user's) and get a text snapshot whose interactive elements have refs like e3. "
        "Use it for pages you must click through or fill in; use fetch_url when reading is enough. It opens in the current tab unless new_tab is true.",
        _obj({"url": {"type": "string"}, "new_tab": {"type": "boolean", "default": False}}, ["url"]), browser_open, "browser", "network",
        examples=[{"url": "https://example.com/pricing"}], taints=True))

    async def browser_snapshot(ctx: dict[str, Any], query: str = "", full: bool = False) -> Any:
        payload: dict[str, Any] = {"full": bool(full), "maxChars": SNAPSHOT_CAP}
        if query:
            payload["query"] = str(query)[:200]
        return await call("browser_snapshot", "snapshot", payload, ctx, 30)

    R("browser_snapshot", ToolSpec("browser_snapshot",
        "Read the current page again as a text snapshot with element refs. Take one after every action that changes the page, because refs change. "
        "Pass query to show only the parts of the page that match a word or phrase; full=true includes content outside the viewport.",
        _obj({"query": {"type": "string"}, "full": {"type": "boolean", "default": False}}, []), browser_snapshot, "browser", "network",
        examples=[{}, {"query": "checkout"}], taints=True))

    async def browser_scroll(ctx: dict[str, Any], direction: str = "down", amount: float = 1, ref: str = "") -> Any:
        if direction not in ("up", "down", "left", "right"):
            return tool_error("browser_scroll: direction must be up, down, left or right", field="direction", example={"direction": "down", "amount": 1})
        try:
            amt = max(0.1, min(float(amount), 20.0))
        except (TypeError, ValueError):
            return tool_error("browser_scroll: amount is a number of screens, e.g. 1", field="amount", example={"direction": "down", "amount": 1})
        payload: dict[str, Any] = {"action": "scroll", "direction": direction, "amount": amt}
        if ref:
            payload["ref"] = str(ref)
        return await call("browser_scroll", "act", payload, ctx, 30)

    R("browser_scroll", ToolSpec("browser_scroll",
        "Scroll the page (or the element ref) by a number of screens. The result is a fresh snapshot.",
        _obj({"direction": {"type": "string", "enum": ["up", "down", "left", "right"], "default": "down"},
              "amount": {"type": "number", "default": 1, "description": "screens"}, "ref": {"type": "string"}}, []),
        browser_scroll, "browser", "network", examples=[{"direction": "down", "amount": 2}], taints=True))

    # ---------------- acting on the page ----------------
    def card(action: str, pv: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
        el = pv.get("element") or {}
        out: dict[str, Any] = {"action": action, "element": f"{el.get('role') or el.get('tag') or 'element'} \"{el.get('name') or ''}\"",
                               "url": pv.get("url") or "", "risk": pv.get("risk")}
        if pv.get("formAction"):
            out["form_action"] = pv["formAction"]
        if pv.get("href"):
            out["href"] = pv["href"]
        return {**out, **extra}

    async def guarded(name: str, action: str, ctx: dict[str, Any], preview: dict[str, Any], act: dict[str, Any],
                      text: str | None = None, force: bool = False) -> Any:
        """Preview, ask when the risk is not 'none', then act. `act` is never sent without the yes."""
        pv = await mac.page_bridge.browser("preview", {"session": session_of(ctx), "action": action, **preview}, 15)
        if not pv.get("ok"):
            return _fail(name, pv)
        risk = str(pv.get("risk") or "none")
        el = pv.get("element") or {}
        secret = risk in ("password", "payment") or str(el.get("inputType") or "").lower() == "password"
        if risk == "upload":
            return tool_error(f"{name}: this element opens a file chooser", alternative="browser_manage(action='upload', ref=..., paths=[...]) with files from the workspace")
        allow_dl = False
        # After reading a page, text the model puts into one can be what the page asked it to leak (the page's own
        # script reads the input). So a tainted run asks before typing, selecting or pressing a printable key there.
        writes = action in ("type", "select") or (action == "press" and len(str(preview.get("key") or "")) == 1)
        leak = bool(ctx.get("tainted")) and writes and not host_allowed(str(pv.get("url") or ""))
        if risk != "none" or force or leak:
            extra = {} if text is None else {"text": "<hidden>" if secret else text}
            if not await ask(ctx, card(action, pv, extra)):
                return tool_error(f"{name}: {DECLINED}" if ctx.get("approve") is not None else f"{name}: could not ask the user; {DECLINED}",
                                  alternative="desk_ask to put the question to the user, or browser_manage(action='handoff')")
            allow_dl = risk == "download"
        payload = {"action": action, **act, **({"allowDownload": True} if allow_dl else {})}
        return await call(name, "act", payload, ctx, 45)

    async def browser_click(ctx: dict[str, Any], ref: str, double: bool = False) -> Any:
        if not ref:
            return tool_error("browser_click: ref is required (e.g. 'e3', from the last snapshot)", field="ref", example={"ref": "e3"})
        return await guarded("browser_click", "click", ctx, {"ref": ref}, {"ref": ref, "double": bool(double)})

    async def browser_type(ctx: dict[str, Any], ref: str, text: str, clear: bool = True, submit: bool = False) -> Any:
        if not ref or text is None:
            return tool_error("browser_type: ref and text are required", example={"ref": "e5", "text": "hello", "submit": False})
        # Typing into a password or payment field always asks (preview reports it); a submit asks via risk 'submit'.
        return await guarded("browser_type", "type", ctx, {"ref": ref, "submit": bool(submit)},
                             {"ref": ref, "text": str(text), "clear": bool(clear), "submit": bool(submit)}, text=str(text))

    async def browser_select(ctx: dict[str, Any], ref: str, values: list[str]) -> Any:
        vals = [values] if isinstance(values, str) else values
        if not ref or not isinstance(vals, list) or not vals:
            return tool_error("browser_select: ref and a list of values are required", example={"ref": "e7", "values": ["Large"]})
        return await guarded("browser_select", "select", ctx, {"ref": ref}, {"ref": ref, "values": [str(v) for v in vals]},
                             text=", ".join(str(v) for v in vals))

    async def browser_press(ctx: dict[str, Any], key: str, ref: str = "") -> Any:
        if not key:
            return tool_error("browser_press: key is required (Enter, Tab, Escape, ArrowDown, Control+a ...)", field="key", example={"key": "Enter"})
        extra = {"ref": ref} if ref else {}
        return await guarded("browser_press", "press", ctx, {"key": key, **extra}, {"key": key, **extra}, text=str(key))

    exec_note = (" It asks the user first when the action submits a form, types into a password or payment field, or starts a download, and before any typing "
                 "or selecting once this chat has read untrusted content (unless the site is on the browser allowlist). "
                 "The result is a fresh snapshot; refs from before are then invalid.")
    R("browser_click", ToolSpec("browser_click", "Click the element with this ref from the last snapshot." + exec_note,
        _obj({"ref": {"type": "string"}, "double": {"type": "boolean", "default": False}}, ["ref"]), browser_click, "browser", "executes",
        examples=[{"ref": "e3"}], taints=True))
    R("browser_type", ToolSpec("browser_type",
        "Type text into the input with this ref (clearing it first unless clear is false). submit=true presses Enter afterwards. "
        "You never type a password the user did not give you in this conversation; for sign-ins use browser_manage(action='handoff')." + exec_note,
        _obj({"ref": {"type": "string"}, "text": {"type": "string"}, "clear": {"type": "boolean", "default": True},
              "submit": {"type": "boolean", "default": False}}, ["ref", "text"]), browser_type, "browser", "executes",
        examples=[{"ref": "e5", "text": "quarterly report", "submit": True}], taints=True))
    R("browser_select", ToolSpec("browser_select", "Choose option(s) in the dropdown with this ref, by label or value." + exec_note,
        _obj({"ref": {"type": "string"}, "values": {"type": "array", "items": {"type": "string"}}}, ["ref", "values"]), browser_select,
        "browser", "executes", examples=[{"ref": "e7", "values": ["Large"]}], taints=True))
    R("browser_press", ToolSpec("browser_press", "Press a key (Enter, Tab, Escape, ArrowDown, Control+a ...), on the element ref or the page." + exec_note,
        _obj({"key": {"type": "string"}, "ref": {"type": "string"}}, ["key"]), browser_press, "browser", "executes",
        examples=[{"key": "Enter", "ref": "e5"}], taints=True))

    # ---------------- tabs, waiting, screenshots, uploads, hand-off ----------------
    def upload_paths(ctx: dict[str, Any], paths: Any) -> list[Path] | dict[str, Any]:
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list) or not paths:
            return tool_error("browser_manage(upload): paths is a list of files from the workspace", field="paths",
                              example={"action": "upload", "ref": "e9", "paths": ["outputs/report.pdf"]})
        g = fsx.grants_for(tb, ctx)
        out: list[Path] = []
        for raw in paths[:10]:
            try:
                p = fsx.resolve_path(raw, g)
                if why := fsx.sensitive_reason(raw, p):
                    raise fsx.FsError(why)
                if not (g.in_desk(p) or g.in_roots(p)):
                    raise fsx.FsError("it is outside the desk workspace and every granted folder")
                if not p.is_file():
                    raise fsx.FsError("it is not a file")
            except fsx.FsError as e:
                return tool_error(redact.scrub_command_output(f"browser_manage(upload): {raw}: {e}"), field="paths",
                                  alternative="desk_list_files to find the file, or desk_write_file to create it first")
            out.append(p)
        return out

    def save_screenshot(ctx: dict[str, Any], res: dict[str, Any]) -> dict[str, Any]:
        try:
            raw = base64.b64decode(str(res.get("pngBase64") or ""), validate=False)
        except ValueError:
            raw = b""
        if not raw:
            return tool_error("browser_manage(screenshot): the browser returned no image", alternative="browser_snapshot to read the page as text")
        root = desk_root(ctx)
        if root is not None:
            d = root / "work" / "screens"
        else:
            d = Path(tempfile.gettempdir()) / "personal-os-screens" / (str(ctx.get("conversation_id") or "none").replace("/", "_"))
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{int(time.time() * 1000)}.png"
        f.write_bytes(raw)
        shown = os.path.relpath(f, root) if root is not None else str(f)
        return _shown({"path": shown, "bytes": len(raw), "width": res.get("width"), "height": res.get("height"),
                       "url": res.get("url") or "", "title": res.get("title") or "",
                       "note": "view_image can look at this file; the image itself is not returned here"})

    async def browser_manage(ctx: dict[str, Any], action: str, tab: int | None = None, ms: int = 1000, text: str = "", accept: bool = True,
                             prompt_text: str = "", full_page: bool = False, ref: str = "", paths: list[str] | None = None,
                             reason: str = "") -> Any:
        if action not in MANAGE_ACTIONS:
            return tool_error(f"browser_manage: unknown action {action!r}", field="action", expected=" | ".join(MANAGE_ACTIONS),
                              example={"action": "tabs"})
        sess = session_of(ctx)
        if action == "close":
            res = await mac.page_bridge.browser("close", {"session": sess}, 15)
            return {"closed": True} if res.get("ok") else _fail("browser_manage", res)
        if action in ("switch_tab", "close_tab"):
            if tab is None:
                return tool_error(f"browser_manage({action}): tab is required (the number from action='tabs')", field="tab",
                                  example={"action": action, "tab": 2})
            return await call("browser_manage", "manage", {"action": action, "tab": int(tab)}, ctx, 20)
        if action == "wait":
            if not text and not (isinstance(ms, int) and 0 < ms <= 30000):
                return tool_error("browser_manage(wait): ms is 1..30000, or pass text to wait for it to appear", field="ms",
                                  example={"action": "wait", "ms": 1500})
            payload: dict[str, Any] = {"action": "wait", "ms": max(0, min(int(ms), 30000))}
            if text:
                payload["text"] = text
            return await call("browser_manage", "manage", payload, ctx, 45)
        if action == "dialog":
            return await call("browser_manage", "manage", {"action": "dialog", "accept": bool(accept), "promptText": prompt_text}, ctx, 20)
        if action == "screenshot":
            res = await mac.page_bridge.browser("manage", {"session": sess, "action": "screenshot", "fullPage": bool(full_page)}, 30)
            return save_screenshot(ctx, res) if res.get("ok") else _fail("browser_manage", res)
        if action == "upload":
            if not ref:
                return tool_error("browser_manage(upload): ref of the file input is required", field="ref",
                                  example={"action": "upload", "ref": "e9", "paths": ["outputs/report.pdf"]})
            files = upload_paths(ctx, paths)
            if isinstance(files, dict):
                return files
            if not await ask(ctx, {"action": "upload", "ref": ref, "files": [p.name for p in files]}):
                return tool_error(f"browser_manage(upload): {DECLINED}", alternative="desk_ask, or browser_manage(action='handoff')")
            return await call("browser_manage", "act", {"action": "upload", "ref": ref, "paths": [str(p) for p in files]}, ctx, 45)
        if action == "handoff":
            return await handoff(ctx, reason, tab)
        payload = {"action": action}  # back / forward / reload / tabs
        return await call("browser_manage", "manage", payload, ctx, 30)

    async def handoff(ctx: dict[str, Any], reason: str = "", tab: int | None = None) -> Any:
        """Show the window, wait on the user's Hand back, hide it, then read the page as they left it."""
        if ctx.get("approve") is None:
            return tool_error("browser_handoff: there is nobody to hand the page to in this run", alternative="finish without it, or say what you could not do")
        sess = session_of(ctx)
        if tab is not None:
            sw = await call("browser_handoff", "manage", {"action": "switch_tab", "tab": int(tab)}, ctx, 20)
            if "error" in sw:
                return sw
        shown = await mac.page_bridge.browser("manage", {"session": sess, "action": "show"}, 15)
        if not shown.get("ok"):
            return _fail("browser_handoff", shown)
        ok = await ask(ctx, {"action": "handoff", "reason": (reason or "the page needs you")[:300], "url": shown.get("url") or ""})
        await mac.page_bridge.browser("manage", {"session": sess, "action": "hide"}, 15)  # whatever the answer, the window goes away
        if not ok:
            return {"handed_back": False, "note": "the user did not take this step; stop this path and desk_ask what they would like instead"}
        out = await call("browser_handoff", "snapshot", {}, ctx, 30)
        if "error" not in out:
            out["handed_back"] = True
            out["notes"] = [*out.get("notes", []), "the user took over the page and handed it back; re-check what changed"]
        return out

    async def browser_handoff(ctx: dict[str, Any], reason: str, tab: int | None = None) -> Any:
        return await handoff(ctx, reason, tab)

    R("browser_handoff", ToolSpec("browser_handoff",
        "Hand the browser window to the user for a password, passkey, two-factor code, CAPTCHA or payment step. It shows the window and waits until they click Hand back, "
        "then returns the page as they left it (handed_back, url, title, snapshot); on Cancel it returns handed_back=false and you stop that path. "
        "Prefer handing off to the user over trying to bypass a login or CAPTCHA. What they type is never shown to you.",
        _obj({"reason": {"type": "string"}, "tab": {"type": "integer"}}, ["reason"]),
        browser_handoff, "browser", "safe",
        examples=[{"reason": "sign in to the airline account"}], taints=True))

    R("browser_manage", ToolSpec("browser_manage",
        "Everything around the page itself. action: back | forward | reload | tabs (list tabs) | switch_tab(tab) | close_tab(tab) | wait(ms or text) | "
        "screenshot (saved as a file in the workspace; look at it with view_image) | dialog(accept, prompt_text) to answer an alert/confirm/prompt | "
        "upload(ref, paths) to attach workspace files to a file input (asks the user) | "
        "handoff(reason) (same as browser_handoff, which you should prefer over bypassing a login or CAPTCHA) to get past a sign-in, CAPTCHA, two-factor prompt or anything you must not do yourself: it shows the browser window to the user "
        "and waits until they have finished. You never type passwords you were not given in this conversation; hand off instead. | close (end the browser session).",
        _obj({"action": {"type": "string", "enum": list(MANAGE_ACTIONS)}, "tab": {"type": "integer"}, "ms": {"type": "integer", "default": 1000},
              "text": {"type": "string"}, "accept": {"type": "boolean", "default": True}, "prompt_text": {"type": "string"},
              "full_page": {"type": "boolean", "default": False}, "ref": {"type": "string"},
              "paths": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}}, ["action"]),
        browser_manage, "browser", "network",
        examples=[{"action": "tabs"}, {"action": "wait", "ms": 1500}, {"action": "handoff", "reason": "sign in to the airline account"},
                  {"action": "upload", "ref": "e9", "paths": ["outputs/report.pdf"]}], taints=True))

    for n in ("browser_open", "browser_snapshot", "browser_scroll", "browser_click", "browser_type", "browser_select", "browser_press",
              "browser_manage", "browser_handoff"):
        tb.specs[n].available_fn = available
