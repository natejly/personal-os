"""Google as a built-in service extension: OAuth sign-in, the read cache, the Calendar, Gmail, Tasks, Drive, Docs
and Sheets client, and the verified writes (client.py). It offers the `google` service; the app and the extensions
that list `google` under requires.services reach the client through it."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .client import Google

if TYPE_CHECKING:
    from ..host import ExtensionScope


def activate(api: ExtensionScope) -> None:
    api.provide("google", Google(api.app.settings, api.app.set_settings, cache_dir=api.db.data_dir))
