"""Settings workspace roots reach every run: a chat folder is added to them, never replaces them."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_tmp = tempfile.mkdtemp()
os.environ["HOME"] = _tmp
os.environ["PERSONAL_OS_DATA_DIR"] = str(Path(_tmp) / "data")

from personal_os import app, shell  # noqa: E402

HOME = Path(_tmp)


def _dirs(*names: str) -> list[Path]:
    out = []
    for n in names:
        p = HOME / n
        p.mkdir(exist_ok=True)
        out.append(p.resolve())
    return out


def test_with_folder_keeps_global_roots():
    g, f = _dirs("global", "chatfolder")
    app.db.set_settings({"workspaceRoots": [str(g)]})
    cfg = app._with_folder(app.settings(), str(f))
    assert cfg["workspaceRoots"] == [str(f), str(g)]
    assert [p for p in shell.granted_roots(cfg, None)] == [f, g]


def test_with_folder_unions_globals_into_partial_cfg():
    g, f = _dirs("global", "chatfolder")
    app.db.set_settings({"workspaceRoots": [str(g)]})
    cfg = app._with_folder({"workspaceRoots": []}, str(f))
    assert cfg["workspaceRoots"] == [str(f), str(g)]


def test_with_folder_dedups_and_never_writes_store():
    g, f = _dirs("global", "chatfolder")
    app.db.set_settings({"workspaceRoots": [str(g), str(f)]})
    cfg = app._with_folder(app.settings(), str(f))
    assert cfg["workspaceRoots"] == [str(f), str(g)]
    assert app.settings()["workspaceRoots"] == [str(g), str(f)]  # stored list untouched


def test_toolbridge_falls_back_to_global_settings():
    import inspect
    from personal_os import toolbridge
    assert 'self.ctx.get("settings") or self.tb.settings()' in inspect.getsource(toolbridge)
