"""workspaceRoots no longer scope anything: the file and shell tools reach the whole Mac, so settings() neither invents
a default folder nor grants the stored ones, and an incoming chat `workingFolder` is simply ignored."""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_tmp = tempfile.mkdtemp()
os.environ["HOME"] = _tmp
os.environ["PERSONAL_OS_DATA_DIR"] = str(Path(_tmp) / "data")

from personal_os import app, permissions  # noqa: E402

HOME = Path(_tmp)


def test_settings_do_not_invent_a_default_folder():
    assert app.settings()["workspaceRoots"] == []
    assert not (HOME / "Grain").exists()  # nothing creates ~/Grain any more


def test_stored_roots_are_kept_and_validated_only_as_a_list_of_strings():
    anywhere = [str(HOME), "/", "/does/not/exist", "~"]
    assert permissions.validate("workspaceRoots", anywhere) == anywhere  # never resolved or refused
    for bad in ("x", [1], [["a"]]):
        try:
            permissions.validate("workspaceRoots", bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should be refused")
    app.db.set_settings({"workspaceRoots": anywhere})
    assert app.settings()["workspaceRoots"] == anywhere


def test_nothing_in_the_app_reads_the_roots_for_scope():
    from personal_os import fsx, shell, snapshots, subagents, system_access, toolbridge, workflows
    for mod in (fsx, shell, snapshots, subagents, system_access, toolbridge, workflows):
        assert "workspaceRoots" not in inspect.getsource(mod), mod.__name__


def test_toolbridge_falls_back_to_global_settings():
    from personal_os import toolbridge
    assert 'self.ctx.get("settings") or self.tb.settings()' in inspect.getsource(toolbridge)
