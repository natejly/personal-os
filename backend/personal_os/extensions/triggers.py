"""Trigger kinds (design doc section 4): what starts a scheduled job besides a clock slot or a folder change.

An extension registers a kind with `api.trigger(kind, adapter)`. A job of that kind has no cron expression; the scheduler
asks the adapter for a look whenever the job's `next_due_at` (the next look, not a slot) has come, and launches the run
when the look found something.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Iterator, Protocol

from .disposables import Disposable

if TYPE_CHECKING:
    from ..jobs import Jobs


class TriggerAdapter(Protocol):
    hint: str  # how a refusal names the kind to the user, e.g. "'calendar' for a calendar event"

    def check(self, job: dict[str, Any]) -> str | None:
        """Why this job's fields cannot be read as this kind (shown to the user), or None when they can."""

    async def poll(self, jobs: Jobs, job: dict[str, Any], at: float) -> tuple[dict[str, Any] | None, float]:
        """One look: the fire record when the trigger holds (None when it does not), and when to look next."""

    def prompt_block(self, fire: dict[str, Any]) -> str:
        """What the fire found, as data for the run's prompt (text others wrote); empty when there is none."""

    # Optional: `async def start_run(self, job) -> str`. A kind whose fire is not a chat run starts its own run when the
    # user runs the job by hand, and returns that run's id; a ValueError says why it could not start.


class Triggers:
    """The trigger kinds extensions registered, by name."""

    def __init__(self) -> None:
        self._adapters: dict[str, tuple[TriggerAdapter, Callable[[], bool]]] = {}

    def __iter__(self) -> Iterator[str]:
        return iter(list(self._adapters))

    def __contains__(self, kind: object) -> bool:
        return kind in self._adapters

    def __getitem__(self, kind: str) -> TriggerAdapter:
        return self._adapters[kind][0]

    def enabled(self, kind: str) -> bool:
        """Whether the extension that registered `kind` is on: a disabled one is not asked for a look."""
        return kind in self._adapters and self._adapters[kind][1]()

    def register(self, kind: str, adapter: TriggerAdapter, enabled: Callable[[], bool]) -> Disposable:
        if kind in self._adapters:
            raise ValueError(f"trigger kind {kind!r} is already registered")
        self._adapters[kind] = adapter, enabled
        return Disposable(lambda: self._adapters.pop(kind, None))
