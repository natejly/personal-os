"""Mail as a built-in extension: the Gmail routes, the agent's mail tools, the delayed send (outbox.py) with its undo hold,
the reply tracker (watch.py), the Today card and recap facts, and the `mail` scheduled-job kind (trigger.py)."""
from __future__ import annotations

import asyncio
import contextlib
import functools
import mimetypes
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import approval_edits, assist, mail_attachments, redact
from ... import blobs as blob_store
from ... import mac
from ...cache import bypass
from ...extract_text import safe_upload_name
from ...todos import Todos
from ...tools import ToolSpec, _obj, page, scrub_strings, tool_error
from ..google.client import NOT_CONNECTED_FACT, GoogleNotConnected, json_safe
from ..google.http import gcall, http_error
from ..disposables import Disposable
from . import edits, watch
from .outbox import Outbox, extras, queued_result, router as outbox_router
from .trigger import MailTrigger
from .watch import MailWatch

if TYPE_CHECKING:
    from ..host import ExtensionScope


class DismissIn(BaseModel):
    dismissed: bool = True


class SnoozeIn(BaseModel):
    until: datetime | None = None  # None clears the snooze


class MailWatchConfigIn(BaseModel):
    awaitingAfterDays: int | None = None
    needsReplyAfterHours: int | None = None
    useLLM: bool | None = None
    query: str | None = None


class GmailModifyIn(BaseModel):
    mark_read: bool | None = None
    archive: bool = False
    star: bool | None = None


class GmailComposeIn(BaseModel):
    to: str
    subject: str = ""
    body: str = ""
    reply_to_message_id: str | None = None
    attachments: list[str] = []  # Uploads document ids
    cc: str | None = None
    bcc: str | None = None


class MailReviewIn(BaseModel):
    to: str = ""
    subject: str = ""
    body: str
    reply_context: str = ""


def _clock() -> datetime:
    return datetime.now(timezone.utc)


class MailExtension:
    def __init__(self, api: ExtensionScope, llm_fn: Callable[[list[dict[str, str]]], Any] | None = None,
                 clock: Callable[[], datetime] = _clock) -> None:
        """`llm_fn` is the optional refinement hook: given [{subject, domain, snippet}] it returns one status per
        item. The app leaves it None, so nothing leaves the machine unless wiring is added deliberately."""
        self.api = api
        self.google = api.services.get("google")
        self.documents = api.services.get("documents")
        self.store = MailWatch(api.db)
        self.todos = Todos(api.db)
        # Every Gmail send is held here first so it can be undone.
        self.outbox = Outbox(api.db, self.google, api.app.settings, documents=self.documents)
        self.llm_fn = llm_fn
        self.clock = clock

    # ---- reply tracker ----
    def config(self) -> dict[str, Any]:
        stored = self.api.app.settings().get("mailWatch") or {}
        return {**watch.DEFAULT_CONFIG, **{k: v for k, v in stored.items() if k in watch.DEFAULT_CONFIG}}

    def refresh_sync(self, force: bool = False) -> int:
        """Pull recent threads, classify, upsert. Runs on a worker thread; raises GoogleNotConnected."""
        cfg = self.config()
        g = self.google
        me = g._me()
        if not me:
            raise GoogleNotConnected("Google is not connected.")
        with bypass() if force else contextlib.nullcontext():
            threads = g.gmail_threads_recent(cfg["query"])
        now = self.clock()
        pairs = [(t, watch.classify(t, me, now, cfg)) for t in threads]
        if cfg["useLLM"] and self.llm_fn is not None and pairs:
            refined = watch.refine(pairs, self.llm_fn, me)
            pairs = [(t, r) for (t, _), r in zip(pairs, refined)]
        return self.store.refresh(pairs)

    # ---- routes ----
    def router(self) -> APIRouter:
        r = APIRouter()
        google = self.google
        call = functools.partial(gcall, google)

        @r.get("/mail/watch/config")
        def get_config() -> dict[str, Any]:
            return self.config()

        @r.put("/mail/watch/config")
        def put_config(body: MailWatchConfigIn) -> dict[str, Any]:
            patch = body.model_dump(exclude_none=True)
            if "awaitingAfterDays" in patch and not 1 <= patch["awaitingAfterDays"] <= 60:
                raise HTTPException(422, "awaitingAfterDays must be between 1 and 60")
            if "needsReplyAfterHours" in patch and not 1 <= patch["needsReplyAfterHours"] <= 24 * 30:
                raise HTTPException(422, "needsReplyAfterHours must be between 1 and 720")
            self.api.app.set_settings({"mailWatch": {**self.config(), **patch}})
            return self.config()

        @r.get("/mail/watch")
        def list_watch(status: str | None = None) -> dict[str, Any]:
            if status is not None and status not in watch.STATUSES:
                raise HTTPException(400, f"status must be one of {', '.join(watch.STATUSES)}")
            now = self.clock()
            return {"threads": self.store.list(status, at=now), "counts": self.store.counts(self.config(), now)}

        @r.post("/mail/watch/refresh")
        async def refresh() -> dict[str, Any]:
            try:
                n = await asyncio.to_thread(self.refresh_sync, True)
            except GoogleNotConnected as e:
                raise HTTPException(409, str(e)) from e
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"Gmail read failed: {e}") from e
            return {"refreshed": n, "counts": self.store.counts(self.config(), self.clock())}

        @r.put("/mail/watch/{thread_id}")
        def dismiss(thread_id: str, body: DismissIn) -> dict[str, Any]:
            if not self.store.dismiss(thread_id, body.dismissed):
                raise HTTPException(404)
            return self.store.get(thread_id)  # type: ignore[return-value]

        @r.post("/mail/threads/{thread_id}/snooze")
        def snooze_thread(thread_id: str, body: SnoozeIn) -> dict[str, Any]:
            """Local only: hides the thread in the mail list until `until` (null clears). No Gmail write."""
            if body.until and not self.store.get(thread_id):
                call(google.gmail_thread_last_id, thread_id)  # an id Gmail does not know must not leave a row behind
            self.store.snooze(thread_id, body.until)
            return {"thread_id": thread_id, "until": body.until}

        @r.get("/mail/snoozed")
        def snoozed() -> dict[str, Any]:
            return {"thread_ids": self.store.snoozed_ids(self.clock())}

        @r.post("/mail/watch/{thread_id}/followup")
        def followup(thread_id: str) -> dict[str, Any]:
            todo = self.store.create_followup(thread_id, self.todos, self.clock().astimezone().date())
            if not todo:
                raise HTTPException(404)
            return todo

        @r.get("/integrations/google/gmail")
        def google_gmail(q: str = "is:unread in:inbox newer_than:14d", max_results: int = 12, refresh: bool = False) -> Any:
            return call(google.gmail_search, q, max_results, refresh=refresh)

        # Registered before the {message_id} route so "labels" is not read as a message id.
        @r.get("/integrations/google/gmail/labels")
        def google_gmail_labels() -> Any:
            return call(google.gmail_labels)

        @r.get("/integrations/google/gmail/{message_id}")
        def google_gmail_message(message_id: str) -> Any:
            return call(google.gmail_get, message_id)

        @r.post("/integrations/google/gmail/{message_id}/modify")
        def google_gmail_modify(message_id: str, body: GmailModifyIn) -> Any:
            return call(google.gmail_modify, message_id, body.mark_read, body.archive, body.star)

        @r.post("/integrations/google/gmail/draft")
        def google_gmail_draft(body: GmailComposeIn) -> Any:
            def draft() -> Any:
                items = mail_attachments.resolve(self.documents, self.api.db.data_dir, body.attachments)  # ValueError -> 400 in gcall
                return google.gmail_draft(body.to, body.subject, body.body, body.reply_to_message_id, **extras(items, body.cc, body.bcc))
            return call(draft)

        @r.post("/integrations/google/gmail/send")
        def google_gmail_send(body: GmailComposeIn) -> Any:
            # Queued, not sent: the hold is what makes Undo possible (see outbox.py). The response says so.
            return call(self.outbox.queue, body.to, body.subject, body.body, body.reply_to_message_id, "app", None, body.attachments, body.cc, body.bcc)

        def attachment(message_id: str, attachment_id: str) -> tuple[dict[str, Any], bytes]:
            """The attachment's name and mime as Gmail lists them for the message (never the client's), and its bytes."""
            att = next((a for a in google.gmail_get(message_id).get("attachments") or [] if a["id"] == attachment_id), None)
            if att is None:
                raise HTTPException(404, "That message has no such attachment.")
            return att, google.gmail_attachment(message_id, attachment_id)

        async def fetch_attachment(message_id: str, attachment_id: str) -> tuple[dict[str, Any], bytes]:
            try:
                return await asyncio.to_thread(attachment, message_id, attachment_id)
            except HTTPException:
                raise
            except GoogleNotConnected as e:
                raise HTTPException(409, str(e)) from e
            except Exception as e:  # noqa: BLE001
                raise http_error(google, e) from e

        @r.post("/integrations/google/gmail/{message_id}/attachments/{attachment_id}/import")
        async def google_gmail_attachment_import(message_id: str, attachment_id: str) -> dict[str, Any]:
            """Copy a received attachment into Uploads so the app can preview it."""
            att, data = await fetch_attachment(message_id, attachment_id)
            return await asyncio.to_thread(self.api.services.get("uploads"), None, att["name"], att["mime"], data)

        @r.post("/integrations/google/gmail/{message_id}/attachments/{attachment_id}/save")
        async def google_gmail_attachment_save(message_id: str, attachment_id: str) -> dict[str, str]:
            """Save a received attachment into the Downloads folder."""
            att, data = await fetch_attachment(message_id, attachment_id)
            dest = await asyncio.to_thread(mail_attachments.save_to_folder, data, att["name"], mail_attachments.downloads_dir())
            return {"path": str(dest), "name": dest.name}

        @r.post("/assist/mail-review")
        async def assist_mail_review(body: MailReviewIn) -> dict[str, Any]:
            try:
                return await assist.review_email(self.api.app.settings(), body.to, body.subject, body.body, body.reply_context)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"Review failed: {e}") from e

        return r

    # ---- tools ----
    def register_tools(self) -> None:
        api, g, documents, outbox = self.api, self.google, self.documents, self.outbox
        run = asyncio.to_thread

        async def mail_followups(ctx: dict[str, Any], kind: str = "awaiting_reply", limit: int = 10, offset: int = 0) -> Any:
            if kind not in ("awaiting_reply", "to_reply"):
                return tool_error(f"Unknown kind '{kind}'.", field="kind", expected="awaiting_reply or to_reply", example={"kind": "to_reply"})
            await run(self.refresh_sync)
            rows = [{"thread_id": t["thread_id"],
                     "subject": redact.scrub_command_output(str(t["subject"] or "")),
                     "last_from": redact.scrub_command_output(str(t["last_from"] or "")),
                     "age_days": round(t["age_days"], 1),
                     "reason": redact.scrub_command_output(str(t["reason"] or ""))}
                    for t in self.store.list(kind, at=self.clock())]
            return page(rows, offset=offset, limit=limit, key="threads")
        api.tool(ToolSpec(
            "mail_followups",
            "Email threads waiting on someone: 'awaiting_reply' = the user sent last and is still waiting on a reply; 'to_reply' = someone wrote and the user owes an answer. Read-only; classified from headers and snippets. Offer follow-up todos but do not create or send anything unprompted.",
            _obj({"kind": {"type": "string", "enum": ["awaiting_reply", "to_reply"], "default": "awaiting_reply"}, "limit": {"type": "integer", "default": 10}, "offset": {"type": "integer", "default": 0}}, []),
            mail_followups, "google",
            examples=[{"kind": "awaiting_reply"}, {"kind": "to_reply", "limit": 5}], taints=True))

        async def gmail_search(ctx: dict[str, Any], query: str = "is:unread in:inbox newer_than:14d", max_results: int = 25, offset: int = 0) -> Any:
            off, n = max(0, int(offset)), max(1, min(int(max_results), 100))
            # One past the page, so page() can tell whether more exist.
            rows = scrub_strings(await run(g.gmail_search, query, off + n + 1))
            return page(rows, offset=off, limit=n, key="messages")
        api.tool(ToolSpec("gmail_search", "Search Gmail with Gmail query syntax (e.g. 'from:alice newer_than:7d'). One call returns a page of up to 25 messages (headers and snippets), newest first; narrow the query rather than paging. Use offset (with the same query and max_results, from next_offset) only when has_more is true and you need a deeper look.",
            _obj({"query": {"type": "string", "default": "is:unread in:inbox newer_than:14d"}, "max_results": {"type": "integer", "default": 25}, "offset": {"type": "integer", "default": 0}}, []), gmail_search, "google",
            examples=[{"query": "is:unread in:inbox newer_than:14d"}, {"query": "from:mira@example.com subject:invoice", "max_results": 5},
                      {"query": "has:attachment newer_than:30d", "max_results": 25, "offset": 25}], taints=True))

        async def gmail_read(ctx: dict[str, Any], message_id: str) -> Any:
            return scrub_strings(await run(g.gmail_get, message_id))
        api.tool(ToolSpec("gmail_read", "Read the full body of an email by id (from gmail_search).",
            _obj({"message_id": {"type": "string"}}, ["message_id"]), gmail_read, "google",
            examples=[{"message_id": "18f2c1a9b7e4d0aa"}], taints=True))

        _ATTACH_PARAM = {"type": "array", "items": {"type": "string"}, "description": "Uploads document ids or file paths on this Mac to attach (25 MB total)"}

        _ADDR_PARAM = {"type": "string", "description": "Optional address list, same format as `to`"}

        def _mail_shown(out: Any) -> Any:
            return scrub_strings(out) if isinstance(out, dict) else out

        def _attach(ctx: dict[str, Any], entries: list[str]) -> list[dict[str, Any]]:
            """Each entry (an Uploads document id, or a file path on this Mac, which is copied into Uploads) as a resolved
            attachment. ValueError names the entry that cannot be attached."""
            pid = ctx.get("project_id") if isinstance(ctx.get("project_id"), str) else None
            ids = []
            for raw in (str(e).strip() for e in entries):
                try:
                    d = documents.get(raw)
                    if d and d["project_id"] and d["project_id"] != pid:
                        raise ValueError("that file belongs to another project")
                    if not d:
                        p = mac.readable_path(raw)
                        if not p.is_file():
                            raise ValueError("not a file")
                        if p.stat().st_size > mail_attachments.MAX_TOTAL_BYTES:
                            raise mail_attachments.AttachmentError(f"{p.name} is over Gmail's 25 MB limit.")
                        data = p.read_bytes()
                        name = safe_upload_name(p.name)
                        dest, digest = blob_store.store(documents.db.data_dir, name, data)
                        d = documents.find_by_hash(pid, digest) or documents.create(
                            pid, name, mimetypes.guess_type(name)[0] or "application/octet-stream", len(data), str(dest), "", content_hash=digest)
                    ids.append(d["id"])
                except (mac.LocalPathError, OSError) as e:
                    raise ValueError(f"{raw}: {e if isinstance(e, mac.LocalPathError) else 'no such document id or file'}") from e
                except ValueError as e:
                    raise ValueError(f"{raw}: {e}") from e
            return ids

        async def _attachments(ctx: dict[str, Any], entries: list[str] | None) -> tuple[list[str], list[dict[str, Any]]] | Any:
            """(document ids, resolved items), or a tool_error for the first entry that cannot be attached."""
            if not entries:
                return [], []
            try:
                ids = await run(_attach, ctx, list(entries))
                return ids, await run(mail_attachments.resolve, documents, documents.db.data_dir, ids)
            except ValueError as e:
                return tool_error(redact.scrub_command_output(f"attachments: {e}"), field="attachments",
                                  expected="Uploads document ids or readable file paths on this Mac, 25 MB in total")

        async def gmail_draft(ctx: dict[str, Any], to: str, subject: str, body: str, reply_to_message_id: str | None = None,
                              attachments: list[str] | None = None, cc: str | None = None, bcc: str | None = None,
                              thread_id: str | None = None) -> Any:
            got = await _attachments(ctx, attachments)
            if isinstance(got, dict):
                return got
            reply_to_message_id = reply_to_message_id or (await run(g.gmail_thread_last_id, thread_id) if thread_id else None)
            return _mail_shown(await run(lambda: g.gmail_draft(to, subject, body, reply_to_message_id, **extras(got[1], cc, bcc))))
        api.tool(ToolSpec("gmail_draft", "Create a Gmail draft (never sends). Prefer this over gmail_send unless the user explicitly asked to send. Files can be attached (25 MB in total).",
            _obj({"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "reply_to_message_id": {"type": "string"}, "thread_id": {"type": "string"}, "attachments": _ATTACH_PARAM,
                  "cc": _ADDR_PARAM, "bcc": _ADDR_PARAM}, ["to", "subject", "body"]), gmail_draft, "google", "external",
            examples=[{"to": "mira@example.com", "subject": "Invoice 42", "body": "Hi Mira,\n\nAttached is invoice 42.\n\nThanks", "attachments": ["<document id>"]},
                      {"to": "team@example.com", "subject": "Re: sprint review", "body": "Works for me.", "reply_to_message_id": "18f2c1a9b7e4d0aa"}]))

        async def gmail_send(ctx: dict[str, Any], to: str, subject: str, body: str, reply_to_message_id: str | None = None,
                             as_draft: bool = False, attachments: list[str] | None = None, cc: str | None = None,
                             bcc: str | None = None, thread_id: str | None = None) -> Any:
            got = await _attachments(ctx, attachments)
            if isinstance(got, dict):
                return got
            # A thread id (as gmail_search returns it) means "reply to the newest message in that thread".
            reply_to_message_id = reply_to_message_id or (await run(g.gmail_thread_last_id, thread_id) if thread_id else None)
            ids, items = got
            if as_draft:
                # The user chose "Save as draft" on the approval card: the same email, written to Drafts and never sent.
                # It still goes through gmail_draft's read-back, so the card can say "Saved to Drafts, verified".
                return _mail_shown(await run(lambda: g.gmail_draft(to, subject, body, reply_to_message_id, **extras(items, cc, bcc))))
            row = await run(outbox.queue, to, subject, body, reply_to_message_id, "assistant", ctx.get("conversation_id"), ids, cc, bcc)
            return queued_result(row)
        gmail_send_spec = ToolSpec("gmail_send", "Queue an email to send from the user's Gmail. It is held for about a minute and a half first so the user can undo it, so it is NOT sent when this returns — say it will go out shortly, never that it is sent. Only when the user explicitly asked to send it. Pass reply_to_message_id (or thread_id, to answer the newest message of that thread) to reply in the thread. Files can be attached (25 MB in total), with optional cc and bcc. Unless Allow everything is on, the user reviews and edits the email on a card first, and only their Send click sends it, so an approved send may differ from what you wrote: the result says what was finally sent, or that it was discarded or saved as a draft. Leave as_draft unset: the user sets it on the card.",
            _obj({"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "reply_to_message_id": {"type": "string"}, "thread_id": {"type": "string"},
                  "as_draft": {"type": "boolean", "default": False}, "attachments": _ATTACH_PARAM, "cc": _ADDR_PARAM, "bcc": _ADDR_PARAM}, ["to", "subject", "body"]), gmail_send, "google", "external",
            examples=[{"to": "mira@example.com", "subject": "Running late", "body": "I will be 10 minutes late."},
                      {"to": "mira@example.com", "subject": "Invoice 42", "body": "Attached.", "attachments": ["~/Documents/invoice-42.pdf"]}])

        # Never sent unasked in Auto or Manual: no allow rule, grant, plan step or reviewer stands in for the user's click. Allow
        # everything sends without it (autoreview.route runs a forced card there); Manual also refuses it by default (STATIC_FLOOR).
        gmail_send_spec.force_ask = gmail_send_spec.force_card = lambda args, ctx: True
        api.tool(gmail_send_spec)

        async def gmail_outbox(ctx: dict[str, Any], action: str = "list", id: str | None = None) -> Any:
            mine = ctx.get("conversation_id")

            def own(row: dict[str, Any]) -> bool:
                # The compose window and other chats are the user's. This tool only touches what this chat queued.
                return bool(mine) and row.get("conversation_id") == mine

            if action == "cancel":
                if not id:
                    return tool_error("cancel needs the id of a queued send.", field="id",
                                      expected="an id from gmail_outbox(action='list')", example={"action": "cancel", "id": "a1b2c3d4"})
                existing = await run(outbox.get, id)
                if not existing or not own(existing):
                    return tool_error("That queued send is not from this chat.", field="id",
                                      alternative="tell the user to use the Undo button on the pending send")
                row = await run(outbox.cancel, id)
                if not row:
                    done = "was already cancelled" if existing["status"] == "cancelled" else "has already gone out"
                    return tool_error(redact.scrub_command_output(
                        f"Send '{id}' can no longer be cancelled — it {done}."), field="id",
                                      alternative="tell the user it was sent, and offer to send a follow-up")
                return scrub_strings({"cancelled": id, "to": row["to"], "subject": row["subject"], "note": "It was never sent."})
            rows = [r for r in await run(outbox.list) if own(r)]

            def _shown(r: dict[str, Any], **extra: Any) -> dict[str, Any]:
                return scrub_strings({"id": r["id"], "to": r["to"], "subject": r["subject"], **extra})

            waiting = [_shown(r, sends_in_seconds=r["seconds_left"]) for r in rows if r["status"] == "holding"]
            recent = [_shown(r, status=r["status"], verified=r["verified"], error=r.get("error"))
                      for r in rows if r["status"] != "holding"][:10]
            return {"waiting": waiting, "count": len(waiting), "recent": recent}
        api.tool(ToolSpec("gmail_outbox", "The emails waiting out their undo hold before Gmail sends them: list them, or cancel one so it never goes out. Use cancel when the user changes their mind about a send you just queued. Sending cannot be hurried from here — only the user can do that.",
            _obj({"action": {"type": "string", "enum": ["list", "cancel"], "default": "list"}, "id": {"type": "string", "description": "the queued send to cancel"}}, []), gmail_outbox, "google", "writes",
            examples=[{}, {"action": "cancel", "id": "a1b2c3d4"}]))

        async def gmail_modify(ctx: dict[str, Any], message_id: str | None = None, mark_read: bool | None = None, archive: bool = False, star: bool | None = None,
                               message_ids: list[str] | None = None) -> Any:
            ids = list(dict.fromkeys(m.strip() for m in [message_id, *(message_ids or [])] if isinstance(m, str) and m.strip()))
            if not ids:
                return {"error": "Pass message_id, or message_ids to change several emails in one call."}
            target = ids if message_ids else ids[0]
            return scrub_strings(await run(g.gmail_modify, target, mark_read, archive, star))
        api.tool(ToolSpec("gmail_modify", "Mark emails read/unread, star them, or archive them. To change more than one email, pass all their ids in message_ids in ONE call (a single batch request, up to 1000 ids) - never one call per email. Use message_id only for a single email.",
            _obj({"message_id": {"type": "string"}, "message_ids": {"type": "array", "items": {"type": "string"}, "description": "several emails changed together in one request"},
                  "mark_read": {"type": "boolean"}, "archive": {"type": "boolean", "default": False}, "star": {"type": "boolean"}}, []), gmail_modify, "google", "external",
            examples=[{"message_ids": ["18f2c1a9b7e4d0aa", "18f2c1a9b7e4d0ab", "18f2c1a9b7e4d0ac"], "mark_read": True, "archive": True},
                      {"message_id": "18f2c1a9b7e4d0aa", "mark_read": True}, {"message_id": "18f2c1a9b7e4d0aa", "star": True}]))

    # ---- recap section ----
    def card(self) -> dict[str, Any]:
        """Today's mail cards: the unread inbox (None until Google is connected, and when the read fails; the log has why) and
        the reply tracker's counts, which come from the table and never call Google."""
        out: dict[str, Any] = {"mail_watch": self.store.counts(self.config(), self.clock()), "gmail": None}
        if not self.google.status()["connected"]:
            return out
        try:
            out["gmail"] = json_safe(self.google.gmail_search("is:unread in:inbox newer_than:14d", 10))
        except Exception:  # noqa: BLE001 - the card is a convenience; the dashboard still answers
            self.api.log.info("the mail card could not read Gmail", exc_info=True)
        return out

    def facts(self) -> dict[str, Any]:
        """The unread inbox for the recap model. A source Grain cannot see says so instead of reading as an empty inbox."""
        if not self.google.status()["connected"]:
            return {"unread_mail": NOT_CONNECTED_FACT}
        try:
            rows = json_safe(self.google.gmail_search("is:unread in:inbox newer_than:14d", 15))
        except Exception as e:  # noqa: BLE001
            return {"unread_mail": f"unavailable: {e}"}
        return {"unread_mail": [{"from": m["from"], "subject": m["subject"]} for m in rows][:10]}

    # ---- outbox ----
    async def start(self, _: Any) -> None:
        """Run the hold timer. The loop's first act is resume(), which decides what a restart does with sends that
        were still waiting (see outbox.py)."""
        self.api.spawn(self.outbox.loop())


def activate(api: ExtensionScope) -> None:
    ext = MailExtension(api)
    api.router(ext.router())
    api.router(outbox_router(ext.outbox))
    ext.register_tools()
    for tool, allow_draft in (("gmail_send", True), ("gmail_draft", False)):
        check = edits.validator(allow_draft)
        approval_edits.register_validator(tool, check)
        api.disposables.add(Disposable(lambda tool=tool, check=check: approval_edits.unregister_validator(tool, check)))
    api.provide("outbox", ext.outbox)
    api.trigger("mail", MailTrigger(ext.google))
    api.recap_section("mail", card=ext.card, facts=ext.facts)
    api.on("app.start", ext.start)
