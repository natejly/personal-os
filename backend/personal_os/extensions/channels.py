"""Channel adapters (design doc section 5.10): a way to talk to Grain from outside the app, such as a phone.

An extension registers an adapter with `api.channel(adapter)`. The app asks the registry which adapter owns a conversation
(the chat that mirrors the channel), tells every adapter when a run changes, and stops them before it cancels the runs.
What an adapter needs from the chat core (start a turn, answer an approval, store an upload) comes in as the `chat` service.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator, Protocol

from .disposables import Disposable


class ChannelAdapter(Protocol):
    name: str  # also the `origin` of a turn that came in through it
    hint: str  # a line for the system prompt of the channel's own chat

    def paired(self) -> bool: ...
    def is_texts_conversation(self, conv_id: str | None) -> bool: ...  # the chat that mirrors the channel
    def texts_conversation_id(self) -> str | None: ...
    def wants_worker_results(self) -> bool: ...  # whether a finished worker's reply is sent to it from any chat
    def on_run_change(self, run: Any) -> None: ...  # never raises
    def from_app(self, conversation_id: str | None, run_id: str, text: str, attachments: list[dict[str, Any]] | None = None) -> None: ...
    def push(self, text: str, attachments: list[dict[str, Any]] | None = None) -> None: ...
    def send_update(self, conversation_id: str | None, text: str, attachments: list[dict[str, Any]] | None = None) -> bool: ...
    async def stop(self) -> None: ...


@dataclass
class ChatService:
    """The chat core's operations an adapter may use, built by the app (late-bound: the chat routes are defined after the
    extensions load)."""
    start_turn: Callable[..., Awaitable[dict[str, Any]]]  # (conversation id, text, attachment ids or None, channel name) -> {"run_id": ...}
    stop: Callable[[str], bool]  # (conversation id)
    decide: Callable[[str, str], Awaitable[dict[str, Any]]]  # (call id, "allow" | "deny", channel name), recorded as that channel's decision
    pending_approvals: Callable[[], list[dict[str, Any]]]
    is_live: Callable[[str], bool]  # a run in this process is waiting on that call id
    is_worker_call: Callable[[str], bool]  # the call id belongs to a background worker
    active_runs: Callable[[], list[dict[str, Any]]]
    create_conversation: Callable[[str], str]  # (title) -> the new conversation's id
    conversation_exists: Callable[[str], bool]
    conversation_title: Callable[[str], str | None]
    mark_conversation: Callable[[str, bool], None]  # (conversation id, is the channel's chat, channel name)
    message_text: Callable[[str], str | None]
    message_attachments: Callable[[str], list[dict[str, Any]]]
    attachment_path: Callable[[str], Path | None]
    store_upload: Callable[[str, str, bytes], Awaitable[dict[str, Any]]]
    call_label: Callable[[str, Any], str | None]
    app_only_tools: frozenset[str]  # tools answered in the app, never on a channel


class Channels:
    """The adapters extensions registered, by name."""

    def __init__(self) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}

    def __iter__(self) -> Iterator[ChannelAdapter]:
        return iter(list(self._adapters.values()))

    def __getitem__(self, name: str) -> ChannelAdapter:
        return self._adapters[name]

    def register(self, adapter: ChannelAdapter) -> Disposable:
        if adapter.name in self._adapters:
            raise ValueError(f"channel {adapter.name!r} is already registered")
        self._adapters[adapter.name] = adapter
        return Disposable(lambda: self._adapters.pop(adapter.name, None))

    def owner_of(self, conv_id: str | None) -> ChannelAdapter | None:
        """The adapter whose chat this conversation is."""
        return next((a for a in self if a.is_texts_conversation(conv_id)), None)

    async def stop(self) -> None:
        for adapter in self:
            await adapter.stop()
