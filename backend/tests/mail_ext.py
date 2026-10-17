"""A Toolbox that carries the mail extension's tools, for tests that call them."""
from __future__ import annotations

import tempfile
from typing import Any, Callable

from calendar_ext import calendar_scope
from personal_os.db import Database
from personal_os.extensions.host import ExtensionContext, ExtensionHost, ExtensionScope
from personal_os.extensions.mail.outbox import Outbox
from personal_os.tools import Toolbox


def mail_scope(google: Any, documents: Any = None, db: Database | None = None, settings: dict[str, Any] | None = None,
               uploads: Any = None, set_settings: Callable[[dict[str, Any]], None] = lambda _s: None) -> ExtensionScope:
    ctx = ExtensionContext(db=db or Database(tempfile.mkdtemp(prefix="mail-ext-")), hidden_views=lambda: (),
                           services={"google": lambda: google, "documents": lambda: documents, "uploads": lambda: uploads},
                           settings=lambda: {} if settings is None else settings, set_settings=set_settings, sid=lambda p: p, wsid=lambda p: p,
                           publish=lambda _n, _d: None)
    return ExtensionHost().load_builtin("mail", ctx)


def mail_tools(google: Any, documents: Any = None, db: Database | None = None,
               settings: dict[str, Any] | None = None) -> tuple[Toolbox, Outbox]:
    """The Toolbox, and the extension's outbox behind its gmail_send and gmail_outbox."""
    scope = mail_scope(google, documents, db, settings)
    box = Toolbox(None, None, documents, lambda: settings or {}, extensions=[calendar_scope(google), scope],  # type: ignore[arg-type]
                  google=google)
    return box, scope.app.services["outbox"]()


def app_outbox(appmod: Any) -> Outbox:
    """The outbox of the app under test."""
    return appmod._ext_ctx.services["outbox"]()
