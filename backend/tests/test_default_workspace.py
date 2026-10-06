"""No ~/Grain: with no folder named, a shell starts in the home folder (or the desk's workspace), and nothing creates a default folder."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp()
os.environ["HOME"] = tmp
os.environ["PERSONAL_OS_DATA_DIR"] = str(Path(tmp) / "data")

from personal_os import app, mac, shell  # noqa: E402

app.settings()
assert not (Path(tmp) / "Grain").exists()
assert not hasattr(mac, "default_workspace") and not hasattr(shell, "granted_roots")
assert shell.resolve_cwd(None, mac.home()) == Path(tmp).resolve()
proj = Path(tmp) / "proj"
proj.mkdir()
assert shell.resolve_cwd("proj", mac.home()) == proj.resolve()
print("ok")
