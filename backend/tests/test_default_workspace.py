"""With no workspaceRoots, settings() falls back to ~/Grain so shell/file tools have a folder; a user root wins."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp()
os.environ["HOME"] = tmp
os.environ["PERSONAL_OS_DATA_DIR"] = str(Path(tmp) / "data")

from personal_os import app, shell  # noqa: E402

s = app.settings()
roots = shell.granted_roots(s, None)
assert roots and roots[0].name == "Grain" and roots[0].is_dir(), roots
assert shell.resolve_cwd(None, roots)[0] == roots[0]

mine = Path(tmp) / "proj"
mine.mkdir()
app.db.set_settings({"workspaceRoots": [str(mine)]})
assert app.settings()["workspaceRoots"] == [str(mine)]
print("ok")
