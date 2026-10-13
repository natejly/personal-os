"""Saved dashboards (saved_dashboards.py): routes, tools, live vs snapshot, binding fallback, pin, @-mention, doc_edit guard.
Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_saved_dashboards.py"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="dashtest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, docs, saved_dashboards as sd, toolbox  # noqa: E402
from personal_os.chatlink import mention_note  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
TABLE = "| date | kcal |\n| --- | --- |\n| 2026-10-01 | 1,800 |\n| 2026-10-02 | 2000.5 |\n"
SPEC = {"type": "line", "x": "date", "series": ["kcal"], "data": "$food"}


def call(name: str, args: dict[str, Any], project_id: str | None = None) -> Any:
    return asyncio.get_event_loop().run_until_complete(toolbox.call(name, args, {"project_id": project_id}))


def mk(title: str, **kw: Any) -> dict[str, Any]:
    body = {"title": title, "bindings": [{"name": "food", "source": "doc", "doc": "Food Log"}],
            "views": [{"kind": "chart", "spec": SPEC}], **kw}
    r = client.post("/saved-dashboards", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def render(id: str) -> dict[str, Any]:
    r = client.get(f"/saved-dashboards/{id}/render")
    assert r.status_code == 200, r.text
    return r.json()


class Dashboards(unittest.TestCase):
    def setUp(self) -> None:
        self.src = docs.create("Food Log", TABLE)

    def tearDown(self) -> None:
        for d in docs.list():
            docs.delete(d["id"])

    def test_save_is_a_doc_with_references_only(self) -> None:
        made = mk("Calories")
        self.assertEqual(made["folder"], "Dashboards")
        d = docs.get(made["id"])
        self.assertEqual(d["folder"], "Dashboards")
        env = json.loads(d["content"])
        self.assertEqual(env["grainDashboard"], 1)
        self.assertNotIn("snapshot", env)
        self.assertNotIn("1800", d["content"])
        self.assertIn(made["id"], [x["id"] for x in client.get("/saved-dashboards").json()])

    def test_spec_render_substitutes_binding_rows(self) -> None:
        r = render(mk("Calories")["id"])
        self.assertEqual(r["views"][0]["spec"]["data"], [{"date": "2026-10-01", "kcal": 1800}, {"date": "2026-10-02", "kcal": 2000.5}])
        self.assertEqual(r["bindings"], [{"name": "food", "source": "doc", "label": "Food Log doc"}])
        self.assertEqual(r["failed"], [])

    def test_html_placeholders_and_escaping(self) -> None:
        docs.save(self.src["id"], content=json.dumps({"rows": [{"k": "<b>x</b>", "n": 2}]}))
        made = client.post("/saved-dashboards", json={
            "title": "Page", "format": "html", "bindings": [{"name": "food", "source": "doc", "doc": "Food Log"}],
            "html": "<p>{{food.n}} {{food.k}} {{food.nope}}|{{nobody}}</p>{{food}}"}).json()
        h = render(made["id"])["html"]
        self.assertIn("<p>2 &lt;b&gt;x&lt;/b&gt; |</p>", h)
        self.assertIn("<td>&lt;b&gt;x&lt;/b&gt;</td>", h)
        self.assertIsNone(render(made["id"])["views"])

    def test_live_refresh_and_failed_binding_fallback(self) -> None:
        id = mk("Calories")["id"]
        first = render(id)
        docs.save(self.src["id"], content=TABLE + "| 2026-10-03 | 1700 |\n")
        second = render(id)
        self.assertEqual(len(second["views"][0]["spec"]["data"]), 3)
        self.assertGreaterEqual(second["updatedAt"], first["updatedAt"])
        docs.delete(self.src["id"])
        third = render(id)
        self.assertEqual(third["views"][0]["spec"]["data"], second["views"][0]["spec"]["data"])
        self.assertEqual([f["binding"] for f in third["failed"]], ["food"])
        self.assertTrue(third["failed"][0]["lastGoodAt"])

    def test_health_binding_with_provider(self) -> None:
        h = sd.health
        h.upsert_synced("steps", date.today().isoformat(), 900, "coros")
        h.log("steps", 50, source="manual")
        b = {"name": "s", "source": "health", "metric": "steps", "days": 7, "provider": "coros"}
        self.assertEqual(sd.resolve({**b, "metric": "steps"}), [{"date": date.today().isoformat(), "value": 900.0}])
        with self.assertRaises(ValueError):
            sd.resolve({**b, "metric": "nope"})

    def test_snapshot_is_frozen_and_render_writes_nothing(self) -> None:
        id = mk("Frozen", mode="snapshot")["id"]
        before = docs.get(id)["content"]
        self.assertEqual(len(json.loads(before)["snapshot"]["data"]["food"]), 2)
        docs.delete(self.src["id"])
        r = render(id)
        self.assertEqual(len(r["views"][0]["spec"]["data"]), 2)
        self.assertEqual(r["failed"], [])
        self.assertEqual(r["updatedAt"], json.loads(before)["snapshot"]["savedAt"])
        self.assertEqual(docs.get(id)["content"], before)

    def test_validation_errors(self) -> None:
        bad: list[dict[str, Any]] = [
            {"title": "x", "views": []},
            {"title": "x", "views": [{"kind": "pie", "spec": {}}]},
            {"title": "x", "bindings": [{"name": "a", "source": "web"}], "views": [{"kind": "chart", "spec": {}}]},
            {"title": "x", "views": [{"kind": "chart", "spec": {"data": "$ghost"}}]},
            {"title": "x", "format": "html", "html": " "},
            {"title": "x", "mode": "snapshot", "bindings": [{"name": "a", "source": "doc", "doc": "Missing"}], "views": [{"kind": "chart", "spec": {}}]},
        ]
        for b in bad:
            self.assertEqual(client.post("/saved-dashboards", json=b).status_code, 400, b)
        self.assertEqual(client.get("/saved-dashboards/nope/render").status_code, 404)
        self.assertEqual(client.get(f"/saved-dashboards/{self.src['id']}/render").status_code, 404)

    def test_pin_moves_scope(self) -> None:
        pid = client.post("/projects", json={"name": "Fit"}).json()["id"]
        id = mk("Calories")["id"]
        out = client.post(f"/saved-dashboards/{id}/pin", json={"project_id": pid}).json()
        self.assertEqual((out["project_id"], docs.get(id)["folder"]), (pid, "Dashboards"))
        self.assertEqual([d["id"] for d in client.get(f"/saved-dashboards?project_id={pid}").json()], [id])
        self.assertIn(pid, {f["scope"] for f in docs.folders() if f["path"] == "Dashboards"})
        call("dashboard_pin", {"name": "Calories", "project": "personal"}, project_id=pid)
        self.assertIsNone(docs.get(id)["project_id"])

    def test_tools_open_and_mention(self) -> None:
        saved = call("dashboard_save", {"title": "Tool Board", "bindings": [{"name": "food", "source": "doc", "doc": "Food Log"}],
                                        "views": [{"kind": "chart", "spec": SPEC}]})
        self.assertEqual(saved["dashboard"]["title"], "Tool Board")
        opened = call("dashboard_open", {"name": "tool board"})
        self.assertEqual(opened["dashboard"], saved["dashboard"])
        self.assertIn("doc_edit", opened["summary"])
        self.assertIn("error", call("dashboard_open", {"name": "ghost"}))
        self.assertIn("error", call("dashboard_save", {"title": "Bad", "views": []}))
        note = mention_note("show @tool-board please", [], "c", sd.list())
        self.assertIn('saved dashboard "Tool Board"', note)
        self.assertIn(f"doc id {saved['doc_id']}", note)
        self.assertEqual(mention_note("no mention", [], "c", sd.list()), "")

    def test_doc_edit_refuses_invalid_dashboard(self) -> None:
        id = mk("Guarded")["id"]
        out = call("doc_edit", {"doc": id, "content": '{"grainDashboard": 1, "views": []}'})
        self.assertIn("error", out)
        self.assertEqual(docs.get(id)["pending"], [])
        ok = json.loads(docs.get(id)["content"])
        ok["views"][0]["spec"]["title"] = "T"
        self.assertEqual(call("doc_edit", {"doc": id, "content": json.dumps(ok)}).get("status"), "pending_review")


if __name__ == "__main__":
    unittest.main()
