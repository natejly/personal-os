"""Pasted images (doc_assets) and the link-title route. Offline: the title fetch is stubbed below the address guard."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="docassets-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod, backups, tools  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20

doc = client.post("/docs", json={"title": "Pics", "content": ""}).json()
did = doc["id"]


def up(name: str, data: bytes, mime: str, doc_id: str = did):
    return client.post(f"/docs/{doc_id}/assets", files={"file": (name, data, mime)})


r = up("shot.png", PNG, "image/png")
assert r.status_code == 200, r.text
url = r.json()["url"]
assert url.startswith(f"/docs/assets/{did}/") and url.endswith("-shot.png")
g = client.get(url)
assert g.status_code == 200 and g.content == PNG and g.headers["content-type"] == "image/png"
assert client.get(url, headers={"X-Personal-OS-Token": "bad"}).status_code == 401
assert up("a.txt", b"hi", "text/plain").status_code == 415
assert up("a.svg", b"<svg/>", "image/svg+xml").status_code == 415
assert up("big.png", b"0" * (8 * 1024 * 1024 + 1), "image/png").status_code == 413
assert up("x.png", PNG, "image/png", "nope").status_code == 404
assert up("../../evil.png", PNG, "image/png").json()["url"].count("/") == 4  # the name is flattened to one segment
assert client.get(f"/docs/assets/{did}/..%2F..%2Fgrain.db").status_code in (400, 404)
assert client.get(f"/docs/assets/{did}/nothere.png").status_code == 404
assert client.get(f"/docs/assets/..%2F{did}/x.png").status_code in (400, 404)

# base64 pastes stay out of the retrieval index
body = "# Heading\n\nwords here ![](data:image/png;base64," + "QUJD" * 500 + ")\n"
client.put(f"/docs/{did}", json={"content": body})
with appmod.docs.db.tx() as c:
    rows = c.execute("SELECT text FROM doc_chunks WHERE doc_id=?", (did,)).fetchall()
assert rows and all("QUJD" not in r["text"] for r in rows)

# backups include the folder
snap = Path(tempfile.mkdtemp()) / "x.zip"
backups.export_zip(appmod.db.data_dir, snap)
assert any(n.startswith(f"doc_assets/{did}/") for n in zipfile.ZipFile(snap).namelist())


# link title: the guard runs first, then the page is read
async def fake_open(client, method, url, host, **kw):
    import httpx
    return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html><title> Hello &amp;\n World </title>")


tools._open_pinned = fake_open
assert asyncio.run(tools.page_title("https://93.184.216.34/a", {})) == "Hello & World"
assert asyncio.run(tools.page_title("http://127.0.0.1:8798/", {})) is None  # loopback refused before any request
assert asyncio.run(tools.page_title("file:///etc/passwd", {})) is None
assert client.post("/docs/link-title", json={"url": "https://93.184.216.34/"}).json() == {"title": "Hello & World"}
print("test_doc_assets: ok")
