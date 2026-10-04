"""Robustness regressions in the MCP client and its tool store.

Runs under pytest, or directly: python backend/tests/test_robustness_mcp.py
Process-backed cases use scripts/mcp_stub.py (local, no network).
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
import unittest.mock
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import mcp_client  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.mcp_client import McpClient, McpTimeout, _Call, _Stderr, _Supervisor, stub_config  # noqa: E402
from personal_os.mcp_servers import MAX_DESCRIPTION_CHARS, McpServers  # noqa: E402


def spec(name: str, desc: str = "d") -> dict[str, Any]:
    return {"name": name, "description": desc, "parameters": {"type": "object"}}


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="mcprobust-")
        self.addCleanup(self._dir.cleanup)
        self.store = McpServers(Database(Path(self._dir.name)))
        self.client = McpClient(self.store, connect_timeout=20.0, call_timeout=10.0)
        self.addAsyncCleanup(self.client.stop)

    def add(self, mode: str, name: str) -> dict[str, Any]:
        cfg = stub_config(mode, args=["--banner", f"rb-{uuid.uuid4().hex[:8]}"])
        return self.store.create_server(name, command=cfg["command"], args=cfg["args"])


class TestToolStore(Base):
    def test_duplicate_names_keep_the_first(self) -> None:
        srv = self.store.create_server("Dup", command="x")
        out = self.store.sync_tools(srv["id"], [spec("a", "first"), spec("a", "second"), spec("a ", "third"), spec("b")])
        self.assertEqual(len(out["added"]), 2)
        tools = {t["name"]: t for t in self.store.tools(srv["id"])}
        self.assertEqual(tools["a"]["description"], "first")
        self.store.sync_tools(srv["id"], [spec("a", "first"), spec("a", "second")])  # and again: no IntegrityError

    def test_description_is_capped_and_hash_is_stable(self) -> None:
        srv = self.store.create_server("Big", command="x")
        long = "x" * 50_000
        self.store.sync_tools(srv["id"], [spec("t", long), spec("s", "short")])
        t1 = {t["name"]: t for t in self.store.tools(srv["id"])}
        self.assertEqual(len(t1["t"]["description"]), MAX_DESCRIPTION_CHARS)
        out = self.store.sync_tools(srv["id"], [spec("t", long), spec("s", "short")])
        self.assertEqual(out["changed"], [], "an unchanged listing must hash the same on every sync")
        t2 = {t["name"]: t for t in self.store.tools(srv["id"])}
        self.assertEqual(t1["s"]["schema_hash"], t2["s"]["schema_hash"])


class TestStderr(unittest.TestCase):
    def test_file_and_memory_stay_bounded(self) -> None:
        lines: list[str] = []
        err = _Stderr(lines)
        try:
            err.file.write(("y" * 900 + "\n") * 5000)  # ~4.5 MB, well past the cap
            err.file.write("z" * 2_000_000)  # one enormous unterminated line
            err.file.flush()
            err.drain()
            self.assertLessEqual(os.fstat(err.file.fileno()).st_size, mcp_client.STDERR_MAX_BYTES)
            self.assertLessEqual(len(lines), mcp_client.STDERR_LINES)
            self.assertTrue(all(len(x) <= mcp_client.STDERR_LINE_CHARS for x in lines))
            err.file.write("after truncation\n")  # append mode: the child's next write still lands
            err.file.flush()
            err.drain()
            self.assertIn("after truncation", lines)
        finally:
            err.close()


class TestCalls(Base):
    async def test_timeout_does_not_hide_the_connector(self) -> None:
        self.add("hostile", "Hostile")
        await self.client.start()
        self.assertTrue(await self.client.wait_ready(20.0))
        before = self.client.ready_slugs()
        self.assertTrue(before)
        with self.assertRaises(McpTimeout):
            await self.client.call("mcp__hostile__hang", {}, timeout=1.0)
        await asyncio.sleep(0.2)
        self.assertEqual(self.client.ready_slugs(), before)
        result = await self.client.call("mcp__hostile__read_document", {"document_id": "x"}, timeout=5.0)
        self.assertEqual(result["content"], "contents of x")

    async def test_an_abandoned_queued_call_is_never_sent(self) -> None:
        srv = self.store.create_server("Q", command="x")
        from personal_os.mcp_client import _config_from
        sup = _Supervisor(self.store, _config_from(self.store, srv["id"]))  # type: ignore[arg-type]
        sup._ready.set()
        sup._task = asyncio.create_task(asyncio.sleep(30))  # looks alive; nothing serves the queue
        self.addCleanup(sup._task.cancel)
        with unittest.mock.patch.object(mcp_client, "CALL_GRACE", 0.0):
            with self.assertRaises(McpTimeout):
                await sup.call("write_thing", {}, timeout=0.2)
        queued = sup._queue.get_nowait()

        class Session:
            sent: list[str] = []

            async def call_tool(self, name: str, *a: Any, **k: Any) -> Any:
                self.sent.append(name)
                raise AssertionError("an abandoned call was sent")

        session = Session()
        await sup._dispatch(session, queued)  # type: ignore[arg-type]
        self.assertEqual(session.sent, [])

    async def test_a_cancelled_caller_abandons_its_call(self) -> None:
        srv = self.store.create_server("Q2", command="x")
        from personal_os.mcp_client import _config_from
        sup = _Supervisor(self.store, _config_from(self.store, srv["id"]))  # type: ignore[arg-type]
        sup._ready.set()
        sup._task = asyncio.create_task(asyncio.sleep(30))
        self.addCleanup(sup._task.cancel)
        t = asyncio.create_task(sup.call("w", {}, timeout=5))
        await asyncio.sleep(0.05)
        t.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await t
        self.assertTrue(sup._queue.get_nowait().future.cancelled())

    async def test_quarantined_tool_is_refused(self) -> None:
        srv = self.store.create_server("Held", command="x")
        self.store.sync_tools(srv["id"], [spec("send")])
        self.store.set_quarantine("mcp__held__send", True)
        with self.assertRaisesRegex(mcp_client.McpUnavailable, "withheld"):
            await self.client.call("mcp__held__send", {})

    async def test_attempts_reset_once_a_connection_is_ready(self) -> None:
        srv = self.add("hostile", "Crashy")
        with unittest.mock.patch.object(mcp_client, "BACKOFF_BASE", 0.05):
            await self.client.start()
            for _ in range(3):
                self.assertTrue(await self.client.wait_ready(20.0))
                with self.assertRaises(mcp_client.McpError):
                    await self.client.call("mcp__crashy__crash", {}, timeout=5.0)
                await asyncio.sleep(0.3)
            self.assertTrue(await self.client.wait_ready(20.0))
        self.assertEqual(self.client.status(srv["id"])[0]["attempts"], 0)

    async def test_concurrent_syncs_leave_one_supervisor_per_server(self) -> None:
        srv = self.add("friendly", "Friendly")
        await self.client.sync()
        self.assertTrue(await self.client.wait_ready(20.0))
        self.store.update_server(srv["id"], {"args": [*srv["args"], "--banner", "changed"]})
        await asyncio.gather(self.client.sync(), self.client.sync())
        live = [t for t in asyncio.all_tasks() if t.get_name().startswith("mcp:") and not t.done()]
        self.assertEqual(len(live), 1, "an overwritten supervisor's child would never be reaped")


class TestRichResults(unittest.TestCase):
    """Images, resources and big text from a tool result reach the model instead of a placeholder."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="mcprich-")
        self.addCleanup(self._dir.cleanup)
        env = unittest.mock.patch.dict(os.environ, {"PERSONAL_OS_DATA_DIR": self._dir.name})
        env.start()
        self.addCleanup(env.stop)

    def test_an_image_is_saved_where_view_image_may_read_it(self) -> None:
        import base64
        from mcp.types import CallToolResult, ImageContent, TextContent
        from personal_os import vision
        png = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"
        out = mcp_client._result_dict(CallToolResult(content=[
            TextContent(type="text", text="chart:"),
            ImageContent(type="image", data=base64.b64encode(png).decode(), mime_type="image/png")]))
        item = out["media"][0]
        p = Path(item["path"])
        self.assertEqual(p.read_bytes(), png)
        self.assertEqual(p.suffix, ".png")
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        self.assertIn("view_image", item["note"])
        self.assertIn(str(p), out["content"])
        self.assertEqual(vision.allowed_image_path(str(p)), p.resolve())
        # ...while the rest of the data folder stays off limits, to view_image and the file tools alike.
        from personal_os import mac
        db = Path(self._dir.name) / "personal-os.db"
        db.write_bytes(b"x")
        with self.assertRaises(mac.LocalPathError):
            vision.allowed_image_path(str(db))
        with self.assertRaises(mac.LocalPathError):
            mac.allowed_path(str(p))

    def test_resources_are_inlined_or_saved(self) -> None:
        import base64
        from mcp.types import BlobResourceContents, CallToolResult, EmbeddedResource, TextResourceContents
        out = mcp_client._result_dict(CallToolResult(content=[
            EmbeddedResource(type="resource", resource=TextResourceContents(uri="file:///notes/a.md", mime_type="text/markdown", text="# Hello")),
            EmbeddedResource(type="resource", resource=BlobResourceContents(uri="file:///x.pdf", mime_type="application/pdf",
                                                                            blob=base64.b64encode(b"%PDF-1").decode()))]))
        self.assertIn("[resource file:///notes/a.md]\n# Hello", out["content"])
        blob = out["media"][0]
        self.assertEqual((blob["uri"], blob["mime_type"]), ("file:///x.pdf", "application/pdf"))
        self.assertEqual(Path(blob["path"]).read_bytes(), b"%PDF-1")

    def test_oversized_base64_is_refused_with_a_note(self) -> None:
        from mcp.types import CallToolResult, ImageContent
        from personal_os import vision
        with unittest.mock.patch.object(mcp_client, "MAX_FILE_BYTES", 10):
            out = mcp_client._result_dict(CallToolResult(content=[ImageContent(type="image", data="QUFB" * 20, mime_type="image/png")]))
        self.assertNotIn("path", out["media"][0])
        self.assertIn("larger than", out["media"][0]["error"])
        self.assertEqual(mcp_client.MAX_FILE_BYTES, vision.MAX_FILE_BYTES)
        self.assertFalse((Path(self._dir.name) / "mcp_media").exists() and any((Path(self._dir.name) / "mcp_media").iterdir()))

    def test_a_big_text_result_is_paged_not_cut(self) -> None:
        from mcp.types import CallToolResult, TextContent
        from personal_os.working import READ_CHARS, ToolResults
        text = "".join(f"line {i:05d}\n" for i in range(6000))  # ~66k chars
        out = mcp_client._result_dict(CallToolResult(content=[TextContent(type="text", text=text)]))
        self.assertEqual(out["content"], text)
        from personal_os.repos import Conversations
        db = Database(Path(self._dir.name))
        tr = ToolResults(db)
        cid = Conversations(db).create(None, "t", "m")["id"]
        shown, rid = tr.render(cid, None, "mcp__x__dump", {k: v for k, v in out.items() if k != "is_error"}, untrusted=True)
        self.assertIsNotNone(rid)
        self.assertIn(rid, shown)
        page2 = tr.read(cid, rid, offset=READ_CHARS)
        self.assertTrue(page2["text"])
        self.assertTrue(page2["has_more"])
        self.assertIn("line 05999", tr.read(cid, rid, offset=page2["total_chars"] - 200)["text"], "nothing was cut off the end")

    def test_old_media_is_swept(self) -> None:
        folder = Path(self._dir.name) / "mcp_media"
        folder.mkdir()
        old, new = folder / "old.png", folder / "new.png"
        old.write_bytes(b"o")
        new.write_bytes(b"n")
        os.utime(old, (1, 1))
        self.assertEqual(mcp_client.sweep_media(), 1)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())

    def test_an_unwritable_media_folder_is_an_error_item_not_a_raise(self) -> None:
        from mcp.types import CallToolResult, ImageContent
        blocker = Path(self._dir.name) / "blocker"
        blocker.write_bytes(b"x")  # a file where the folder's parent should be
        with unittest.mock.patch.object(mcp_client.mac, "mcp_media_dir", return_value=blocker / "mcp_media"):
            out = mcp_client._result_dict(CallToolResult(content=[ImageContent(type="image", data="QUFB", mime_type="image/png")]))
        self.assertNotIn("path", out["media"][0])
        self.assertIn("not saved", out["media"][0]["error"])

    def test_a_repeated_image_refreshes_its_mtime(self) -> None:
        from mcp.types import CallToolResult, ImageContent
        block = CallToolResult(content=[ImageContent(type="image", data="QUFB", mime_type="image/png")])
        p = Path(mcp_client._result_dict(block)["media"][0]["path"])
        os.utime(p, (1, 1))
        mcp_client._result_dict(block)
        self.assertEqual(mcp_client.sweep_media(), 0)
        self.assertTrue(p.exists())


if __name__ == "__main__":
    unittest.main()
