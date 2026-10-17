"""A Toolbox that carries the calendar extension's tools, for tests that call them."""
from __future__ import annotations

import tempfile
from typing import Any

from personal_os.db import Database
from personal_os.extensions.host import ExtensionContext, ExtensionHost, ExtensionScope
from personal_os.tools import Toolbox


def calendar_scope(google: Any, extundo: Any = None, memories: Any = None) -> ExtensionScope:
    ctx = ExtensionContext(db=Database(tempfile.mkdtemp(prefix="calendar-ext-")), hidden_views=lambda: (),
                           services={"google": lambda: google, "extundo": lambda: extundo, "memories": lambda: memories},
                           settings=lambda: {}, set_settings=lambda _s: None, sid=lambda p: p, wsid=lambda p: p,
                           publish=lambda _n, _d: None)
    return ExtensionHost().load_builtin("calendar", ctx)


def calendar_box(google: Any, extundo: Any = None, memories: Any = None, settings: dict[str, Any] | None = None) -> Toolbox:
    return Toolbox(None, None, None, lambda: settings or {}, extensions=[calendar_scope(google, extundo, memories)],  # type: ignore[arg-type]
                   google=google, extundo=extundo)
