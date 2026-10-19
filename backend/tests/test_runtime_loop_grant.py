"""A user-tier extension may provide `agent.loop` only when the user granted it `runtime:loop`; the import fence still applies.

Run: python -m pytest backend/tests/test_runtime_loop_grant.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="loopgrant-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os.extensions.hooks import HookBus  # noqa: E402
from personal_os.extensions.authoring.scaffold import scaffold  # noqa: E402
from personal_os.runtime.slots import Provider, Runtime  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
review = appmod.ext_review
EXT = "rl-loop"


def on_loop(coro: Any) -> Any:
    return asyncio.run_coroutine_threadsafe(coro, appmod._loop).result(timeout=30)  # type: ignore[arg-type]


@pytest.fixture(scope="module", autouse=True)
def _app():  # type: ignore[no-untyped-def]
    with client:
        yield


@pytest.fixture(autouse=True)
def _clean():  # type: ignore[no-untyped-def]
    yield
    for ext_id in review.drafts.installed_ids():
        on_loop(review.stand_down(ext_id))
    for folder in (review.drafts.installed, review.drafts.drafts):
        shutil.rmtree(folder, ignore_errors=True)
    appmod.db.set_settings({"hiddenViews": [v for v in appmod.settings()["hiddenViews"] if not v.startswith("rl-")]})


def install_loop_extension() -> None:
    root = scaffold(EXT, review.drafts.drafts)
    manifest = json.loads((root / "extension.json").read_text("utf-8"))
    manifest["permissions"] += ["runtime:loop"]
    manifest.setdefault("contributes", {})["runtime"] = [{"slot": "agent.loop", "id": "mine", "label": "Mine"}]
    (root / "extension.json").write_text(json.dumps(manifest), "utf-8")
    on_loop(appmod.toolbox.call("ext_propose", {"id": EXT}, {"conversation_id": None, "project_id": None, "taint_sources": []}))
    digest = next(p["digest"] for p in client.get("/extensions").json()["proposals"] if p["id"] == EXT)
    assert client.post(f"/extensions/{EXT}/accept", json={"digest": digest}).status_code == 200


def enable(grants: list[str]) -> Any:
    return client.post(f"/extensions/{EXT}/enable", json={"grants": grants})


def providers() -> list[str]:
    return [p.id for p in appmod.host.runtime.providers("agent.loop")]


def test_the_review_card_asks_for_the_loop_grant() -> None:
    install_loop_extension()
    card = next(e for e in client.get("/extensions").json()["installed"] if e["id"] == EXT)
    assert card["needsGrant"] == ["runtime:loop"]


def test_a_user_loop_is_refused_without_the_grant_and_accepted_with_it() -> None:
    install_loop_extension()
    assert enable([]).status_code == 409
    assert f"{EXT}/mine" not in providers()
    assert enable(["runtime:loop"]).status_code == 200
    assert f"{EXT}/mine" in providers()


def test_revoking_removes_the_provider_and_the_grant_must_be_given_again() -> None:
    install_loop_extension()
    assert enable(["runtime:loop"]).status_code == 200
    on_loop(review.stand_down(EXT))
    assert f"{EXT}/mine" not in providers()
    assert review.drafts.slot_grants(EXT) == []
    assert enable([]).status_code == 409


def test_a_load_without_the_grant_registers_nothing() -> None:
    install_loop_extension()
    host = appmod.host
    assert EXT not in host.slot_grants
    root = review.drafts.installed / EXT
    with pytest.raises(PermissionError):
        on_loop(host.load_dev(root, review.ctx, first_load=False))
    assert f"{EXT}/mine" not in providers()


def test_a_built_in_loop_is_unaffected() -> None:
    assert "core/chat-loop" in providers()
    rt = Runtime(HookBus(), [])
    rt.declare(Provider("x/loop", "agent.loop", "X", "built-in", "x"))
    with pytest.raises(PermissionError):
        rt.declare(Provider("u/loop", "agent.loop", "U", "user", "u"))
    rt.declare(Provider("u/loop", "agent.loop", "U", "user", "u"), granted=True)
    with pytest.raises(PermissionError):  # the grant opens agent.loop only
        rt.declare(Provider("u/sb", "sandbox.backend", "S", "user", "u"), granted=True)


def test_the_fence_still_rejects_a_forbidden_import_in_a_user_loop() -> None:
    path = Path(os.environ.get("TMPDIR", tempfile.gettempdir())) / "fenced_user_loop.py"
    path.write_text(
        "import gc\n\n\nclass Loop:\n    id = 'u/loop'\n    bounds: dict = {}\n\n    async def run(self, turn, h):\n        yield ('delta', {})\n", "utf-8")
    spec = importlib.util.spec_from_file_location("fenced_user_loop", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["fenced_user_loop"] = module
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        rt = Runtime(HookBus(), [])
        rt.declare(Provider("u/loop", "agent.loop", "U", "user", "u"), granted=True)
        with pytest.raises(ValueError):
            rt.attach("agent.loop", "u/loop", module.Loop())
    finally:
        sys.modules.pop("fenced_user_loop", None)
        path.unlink(missing_ok=True)
