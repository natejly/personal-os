"""The Python SDK for Grain extensions. An extension imports from here and never from `personal_os`; `grain ext check`
enforces that.

`Extension` is the `api` object `activate(api)` receives. `types` holds the manifest and every hook payload, generated
from the JSON Schemas in schemas/ (see generate.py); `testing.Harness` drives an extension in a scratch backend.
"""
from personal_os.context import Contribution
from personal_os.extensions.hooks import Ask, Deny
from personal_os.extensions.host import ExtensionScope as Extension
from personal_os.tools import ToolSpec

from . import types

__all__ = ["Ask", "Contribution", "Deny", "Extension", "ToolSpec", "types"]
