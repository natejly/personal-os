"""The shared work environment: a Python venv with the document and data libraries, and python_install."""
from __future__ import annotations

from typing import Any


def register(tb: Any) -> None:
    """Register this module's tools on the Toolbox."""
