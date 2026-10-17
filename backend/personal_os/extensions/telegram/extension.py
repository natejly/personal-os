"""Telegram as a built-in extension: the bot bridge (telegram.py) as a channel adapter, its settings routes, and the
hooks around it.

It is the one channel adapter today. A message from the paired chat fires `channel.inbound` before the turn starts; a
reply or a worker's result passes through `channel.outbound` before it is sent, where a handler may reword it or cancel it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import telegram
from ..hooks import Cancel

if TYPE_CHECKING:
    from ..host import ExtensionScope


class TokenIn(BaseModel):
    token: str


class EnabledIn(BaseModel):
    enabled: bool


class TelegramExtension:
    def __init__(self, api: ExtensionScope) -> None:
        self.api, self.db = api, api.db
        self.chat = api.services.get("chat")
        self.bridge = telegram.TelegramBridge(telegram.Deps(
            get_settings=api.app.settings,
            load_state=lambda: self.db.get_settings().get("telegramState") or {},
            save_state=lambda st: self.db.set_settings({"telegramState": st}),
            get_token=lambda: self.db.secrets.get(telegram.SECRET_NAME),
            start_turn=self.start_turn,
            stop=self.chat.stop,
            decide=lambda call_id, decision: self.chat.decide(call_id, decision, self.bridge.name),
            pending_approvals=self.chat.pending_approvals,
            is_live=self.chat.is_live,
            active_runs=self.chat.active_runs,
            create_texts_conversation=lambda: self.chat.create_conversation("Telegram"),
            conversation_exists=self.chat.conversation_exists,
            message_text=self.chat.message_text,
            app_only_tools=self.chat.app_only_tools,
            conversation_title=self.chat.conversation_title,
            message_attachments=self.chat.message_attachments,
            attachment_path=self.chat.attachment_path,
            store_upload=self.chat.store_upload,
            mark_texts_conversation=lambda cid, flag: self.chat.mark_conversation(cid, flag, self.bridge.name),
            computer_name=telegram.computer_name,
            call_label=self.chat.call_label,
            outbound=self.outbound,
        ))

    async def start_turn(self, conv_id: str, text: str, attachment_ids: list[str] | None = None) -> dict[str, Any]:
        await self.api.bus.emit("channel.inbound", {"channel": self.bridge.name, "sender": "owner", "text": text,
                                                    "files": list(attachment_ids or [])})
        return await self.chat.start_turn(conv_id, text, attachment_ids, self.bridge.name)

    async def outbound(self, text: str, atts: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]] | None:
        """Chain `channel.outbound` over the reply: a handler returns the payload with `text` and `files` changed, or Cancel."""
        sent = await self.api.bus.pipe("channel.outbound", {"channel": self.bridge.name, "text": text, "files": [dict(a) for a in atts]})
        if isinstance(sent, Cancel):
            return None
        if not isinstance(sent, dict) or not isinstance(sent.get("text"), str):
            return text, atts
        files = sent.get("files")
        return sent["text"], files if isinstance(files, list) else atts

    async def start(self, _: Any) -> None:
        if self.api.app.settings().get("telegramEnabled") and self.db.secrets.get(telegram.SECRET_NAME):
            await self.bridge.start()

    async def settings_changed(self, ev: dict[str, Any]) -> None:
        if "telegramEnabled" in ev["keys"]:
            await self.bridge.reconcile()

    def approval_requested(self, ev: dict[str, Any]) -> None:
        """A background worker has no run of its own to text from, so its card is sent as soon as it opens."""
        if self.chat.is_worker_call(ev["call_id"]):
            self.bridge.notify_worker_approval(ev["call_id"])

    def router(self) -> APIRouter:
        r, bridge, db = APIRouter(prefix="/telegram"), self.bridge, self.db

        @r.get("/status")
        def status() -> dict[str, Any]:
            return bridge.status()

        @r.put("/token")
        async def put_token(body: TokenIn) -> dict[str, Any]:
            token = body.token.strip()
            if not telegram.TOKEN_RE.fullmatch(token):
                raise HTTPException(422, "That does not look like a bot token")
            try:
                me = await bridge.check_token(token)
            except telegram.TelegramError as e:
                if e.code in (401, 404):
                    raise HTTPException(400, "Telegram rejected that token") from None
                raise HTTPException(502, f"Could not reach Telegram: {telegram.sanitize(e.description, token)}") from None
            db.secrets.set(telegram.SECRET_NAME, token)
            bridge.adopt(me)
            db.set_settings({"telegramEnabled": True})
            await bridge.reconcile()
            return bridge.status()

        @r.delete("/token")
        async def delete_token() -> dict[str, Any]:
            await bridge.stop()
            db.secrets.delete(telegram.SECRET_NAME)
            bridge.clear()
            return bridge.status()

        @r.post("/pairing")
        def pairing() -> dict[str, Any]:
            if not db.secrets.get(telegram.SECRET_NAME):
                raise HTTPException(400, "Add a bot token first")
            bridge.issue_pairing()
            return bridge.status()

        @r.post("/takeover")
        async def takeover() -> dict[str, Any]:
            if not db.secrets.get(telegram.SECRET_NAME):
                raise HTTPException(400, "Add a bot token first")
            await bridge.take_over()
            return bridge.status()

        @r.post("/unpair")
        def unpair() -> dict[str, Any]:
            bridge.unpair()
            return bridge.status()

        @r.post("/test")
        async def send_test() -> dict[str, Any]:
            res = await bridge.send_test()
            if res.get("error") == "not_paired":
                raise HTTPException(400, "Pair a Telegram chat first")
            return res

        @r.post("/enabled")
        async def set_enabled(body: EnabledIn) -> dict[str, Any]:
            db.set_settings({"telegramEnabled": body.enabled})
            await bridge.reconcile()
            return bridge.status()

        return r


def activate(api: ExtensionScope) -> None:
    ext = TelegramExtension(api)
    api.channel(ext.bridge)
    api.router(ext.router())
    api.on("app.start", ext.start)
    api.on("settings.changed", ext.settings_changed)
    api.on("approval.requested", ext.approval_requested)
