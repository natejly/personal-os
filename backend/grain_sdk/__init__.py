"""The Python SDK for Grain extensions. An extension imports from here and never from `personal_os`; `grain ext check`
enforces that.

`Extension` is the `api` object `activate(api)` receives. `types` holds the manifest type, generated from the JSON Schema in
schemas/ (see generate.py); `hooks` holds every hook payload and the typed `api.on` and `Harness.fire`, generated from the
hook catalog (`grain ext gen-hooks`); `testing.Harness` drives an extension in a scratch backend.
"""
from personal_os.context import Contribution
from personal_os.extensions.hooks import Ask, Deny, HookCtx
from personal_os.extensions.host import ExtensionScope
from personal_os.extensions.notes import InboxItem
from personal_os.extensions.trashkinds import TrashHandler
from personal_os.tools import ToolSpec

from . import hooks, types
from .hooks import On


class Extension(ExtensionScope):
    """The `api` object `activate(api)` receives."""
    on: On  # type: ignore[assignment]  # the same method, typed per hook


__all__ = ["Ask", "Contribution", "Deny", "Extension", "HookCtx", "InboxItem", "ToolSpec", "TrashHandler", "hooks", "types"]
