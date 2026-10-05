"""One active mail + calendar provider.

Settings `pimProvider` names the account the Mail and Calendar views, the agent's mail and
calendar tools, the reply tracker and the undo outbox talk to: "google" (default) or
"microsoft". Everything the Microsoft side does not have (Tasks, Drive, Docs, Sheets)
keeps going to Google regardless, so the todo sync and the file tools work unchanged.

`Pim` stands where the `Google` object used to be passed, so one switch covers every caller.
"""
from __future__ import annotations

from typing import Any, Callable

PROVIDERS = ("google", "microsoft")
# Attribute prefixes that follow the active provider; anything else is Google-only.
_SWITCHED = ("calendar", "gmail_", "enabled_calendar_ids", "status", "invalidate", "cache_stats", "forget")


def active_provider(settings: dict[str, Any]) -> str:
    p = settings.get("pimProvider")
    return p if p in PROVIDERS else "google"


class Pim:
    def __init__(self, google: Any, microsoft: Any, get_settings: Callable[[], dict[str, Any]]):
        self.google = google
        self.microsoft = microsoft
        self._settings = get_settings

    @property
    def provider(self) -> str:
        return active_provider(self._settings())

    @property
    def active(self) -> Any:
        return self.microsoft if self.provider == "microsoft" else self.google

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        target = self.active if name.startswith(_SWITCHED) else self.google
        return getattr(target, name)
