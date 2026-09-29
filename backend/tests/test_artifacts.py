"""Artifact parsing, versioning and the capability-scoped render routes.

Runs against a throwaway data dir: PERSONAL_OS_DATA_DIR is set before personal_os.app is imported,
because that module opens the database at import time.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="personal-os-test-"))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as A  # noqa: E402
from personal_os import artifacts as M  # noqa: E402

client = TestClient(A.app)
AUTH = {"X-Personal-OS-Token": A.AUTH_TOKEN}


def conv(title: str) -> str:
    """A real conversation row: artifacts.conversation_id is a foreign key, and it is enforced."""
    return A.convos.create(None, title, "test-model")["id"]


def spec(identifier: str = "demo", content: str = "<h1>hi</h1>", kind: str = "html", title: str = "Demo") -> dict:
    return {"identifier": identifier, "title": title, "kind": kind, "lang": "", "content": content}


# ---- parsing ----
def test_parses_header_and_body():
    out = M.parse('```artifact\n{"id":"a-1","title":"T","kind":"react"}\nfunction App(){}\n```')
    assert len(out) == 1
    assert out[0]["identifier"] == "a-1" and out[0]["kind"] == "react" and out[0]["title"] == "T"
    assert out[0]["content"] == "function App(){}"


def test_prose_around_blocks_is_ignored():
    out = M.parse('before\n\n```artifact\n{"id":"x"}\n<p>1</p>\n```\n\nafter\n')
    assert [a["identifier"] for a in out] == ["x"]


def test_longer_fence_allows_inner_backticks():
    text = '````artifact\n{"id":"x","kind":"markdown"}\nsee ```code``` here\n````'
    assert M.parse(text)[0]["content"] == "see ```code``` here"


def test_unterminated_block_is_dropped():
    assert M.parse('```artifact\n{"id":"x"}\n<p>half') == []


def test_last_block_wins_within_one_reply():
    text = ('```artifact\n{"id":"x"}\n<p>first</p>\n```\n'
            '```artifact\n{"id":"x"}\n<p>second</p>\n```')
    out = M.parse(text)
    assert len(out) == 1 and out[0]["content"] == "<p>second</p>"


def test_missing_header_still_yields_an_artifact():
    out = M.parse("```artifact\n<p>bare</p>\n```")
    assert len(out) == 1 and out[0]["content"] == "<p>bare</p>"


def test_empty_block_is_skipped():
    assert M.parse('```artifact\n{"id":"x"}\n\n```') == []


def test_kind_aliases_and_unknown_kinds_fall_back_to_html():
    assert M.parse('```artifact\n{"id":"x","kind":"jsx"}\nz\n```')[0]["kind"] == "react"
    assert M.parse('```artifact\n{"id":"x","kind":"wat"}\nz\n```')[0]["kind"] == "html"


def test_identifier_falls_back_to_the_title():
    assert M.parse('```artifact\n{"title":"My Big Plan"}\nz\n```')[0]["identifier"] == "my-big-plan"


# ---- rendering ----
def test_full_html_document_is_passed_through():
    doc = M.document("html", "<!DOCTYPE html><html><body>x</body></html>")
    assert doc.startswith("<!DOCTYPE html>")


def test_html_fragment_is_wrapped():
    doc = M.document("html", "<b>x</b>", "T", "dark")
    assert 'data-theme="dark"' in doc and "<b>x</b>" in doc and "artifact-pad" in doc


def test_react_imports_stripped_and_component_mounted():
    doc = M.document("react", "import React from 'react'\nexport default function App(){return <i/>}")
    assert "import React" not in doc
    assert "function App()" in doc
    assert "ReactDOM.createRoot" in doc


def test_react_default_export_expression_is_captured():
    doc = M.document("react", "export default () => <i/>")
    assert "var __default__ = " in doc


def test_script_tag_in_artifact_cannot_break_out():
    doc = M.document("react", "function App(){}\n// </script><img src=x onerror=alert(1)>")
    inner = doc.split('data-presets="react">')[1].split("</script>")[0]
    assert "</script" not in inner


def test_code_and_markdown_are_escaped():
    doc = M.document("code", "<script>alert(1)</script>", "f", "light", "python")
    assert "&lt;script&gt;" in doc and "<script>alert(1)" not in doc


# ---- storage ----
def test_resaving_identical_content_does_not_burn_a_version():
    a, changed = A.artifacts.save(spec("ver-1"))
    assert changed and a["version"] == 1
    b, changed = A.artifacts.save(spec("ver-1"))
    assert not changed and b["version"] == 1
    c, changed = A.artifacts.save(spec("ver-1", content="<h1>new</h1>"))
    assert changed and c["version"] == 2


def test_same_identifier_in_two_conversations_is_two_artifacts():
    x, _ = A.artifacts.save(spec("shared"), conversation_id=conv("a"))
    y, _ = A.artifacts.save(spec("shared"), conversation_id=conv("b"))
    assert x["id"] != y["id"]


def test_an_artifact_dies_with_its_conversation():
    cid = conv("doomed")
    a, _ = A.artifacts.save(spec("orphan"), conversation_id=cid)
    A.convos.delete(cid)
    assert A.artifacts.get(a["id"]) is None


def test_revert_appends_rather_than_rewriting_history():
    a, _ = A.artifacts.save(spec("rev-1", content="v1"))
    A.artifacts.update(a["id"], {"content": "v2"})
    back = A.artifacts.revert(a["id"], 1)
    assert back["content"] == "v1" and back["version"] == 3
    assert [v["version"] for v in A.artifacts.versions(a["id"])] == [3, 2, 1]


# ---- routes ----
def test_render_requires_a_valid_capability():
    a, _ = A.artifacts.save(spec("route-1"))
    aid = a["id"]
    assert client.get(f"/artifacts/{aid}/render").status_code == 401
    assert client.get(f"/artifacts/{aid}/render?t=bad&e=99999999999").status_code == 401
    url = client.get(f"/artifacts/{aid}", headers=AUTH).json()["render_url"]
    r = client.get(url)
    assert r.status_code == 200
    assert "connect-src 'none'" in r.headers["content-security-policy"]


def test_expired_capability_is_refused():
    a, _ = A.artifacts.save(spec("route-2"))
    aid = a["id"]
    exp = 1000
    tok = A._render_token("artifacts", aid, exp)
    assert client.get(f"/artifacts/{aid}/render?t={tok}&e={exp}").status_code == 401


def test_a_capability_does_not_unlock_another_artifact():
    a, _ = A.artifacts.save(spec("route-3a"))
    b, _ = A.artifacts.save(spec("route-3b"))
    url = client.get(f"/artifacts/{a['id']}", headers=AUTH).json()["render_url"]
    qs = url.split("?", 1)[1]
    assert client.get(f"/artifacts/{b['id']}/render?{qs}").status_code == 401


def test_render_and_crud_round_trip():
    a, _ = A.artifacts.save(spec("route-4", content="<h1>one</h1>"))
    aid = a["id"]
    r = client.put(f"/artifacts/{aid}", json={"content": "<h1>two</h1>"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["version"] == 2
    assert [v["version"] for v in client.get(f"/artifacts/{aid}/versions", headers=AUTH).json()] == [2, 1]
    old = client.get(client.get(f"/artifacts/{aid}", headers=AUTH).json()["render_url"] + "&version=1")
    assert "one" in old.text
    assert client.delete(f"/artifacts/{aid}", headers=AUTH).json() == {"ok": True}
    assert client.get(f"/artifacts/{aid}", headers=AUTH).status_code == 404


def test_preview_stages_an_unsaved_document():
    r = client.post("/artifacts/preview", json={"content": "<b>draft</b>", "kind": "html"}, headers=AUTH)
    assert r.status_code == 200
    page = client.get(r.json()["render_url"])
    assert page.status_code == 200 and "draft" in page.text


def test_preview_refuses_an_oversized_document():
    body = {"content": "x" * (A.PREVIEW_MAX_BYTES + 1), "kind": "html"}
    assert client.post("/artifacts/preview", json=body, headers=AUTH).status_code == 413


def test_preview_cache_is_capped():
    for i in range(M.KINDS and A.PREVIEW_MAX + 10):
        client.post("/artifacts/preview", json={"content": f"<b>{i}</b>", "kind": "html"}, headers=AUTH)
    assert len(A._previews) <= A.PREVIEW_MAX


def test_conversation_listing_is_scoped():
    cid = conv("listing")
    A.artifacts.save(spec("conv-scoped"), conversation_id=cid)
    A.artifacts.save(spec("elsewhere"), conversation_id=conv("other"))
    rows = client.get(f"/conversations/{cid}/artifacts", headers=AUTH).json()
    assert [r["identifier"] for r in rows] == ["conv-scoped"]
    assert all("render_url" in r for r in rows)


def test_save_from_reply_persists_every_block():
    text = ('```artifact\n{"id":"r-1","title":"One"}\n<p>1</p>\n```\n'
            'words\n'
            '```artifact\n{"id":"r-2","title":"Two","kind":"svg"}\n<svg/>\n```')
    rows = A.artifacts.save_from_reply(text, conv("reply"), None, "msg-1")
    assert [r["identifier"] for r in rows] == ["r-1", "r-2"]
    assert rows[1]["kind"] == "svg"


def test_the_render_hint_documents_the_block():
    assert "```artifact" in A.RENDER_HINT and '"kind"' in A.RENDER_HINT
