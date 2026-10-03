"""Provider retry/backoff, stream idle and deadline limits, log redaction and rotation, the retention sweep, diagnostics.

Run: python backend/tests/test_reliability.py   (or pytest)
The provider is a httpx.MockTransport: nothing here touches the network or sleeps for real.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import sys
import tempfile
import time
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="reliability-"))

import httpx  # noqa: E402

import importlib.util  # noqa: E402

from personal_os import llm as app_llm, logs  # noqa: E402

# Other test modules replace llm.stream_chat with a script and never put it back, so under one pytest process
# the shared module cannot be trusted. A private copy of the source is always the real thing.
_spec = importlib.util.spec_from_file_location("personal_os._llm_under_test", app_llm.__file__)
llm = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(llm)  # type: ignore[union-attr]
from personal_os.db import Database  # noqa: E402
from personal_os.repos import Conversations  # noqa: E402
from personal_os.retention import sweep  # noqa: E402
from personal_os.working import ToolResults  # noqa: E402

REAL_BACKOFF = llm._backoff
SETTINGS: dict[str, Any] = {"baseUrl": "http://provider.test", "apiKey": "sk-test-secret-0123456789", "llmRetries": 3}


def sse(*texts: str) -> bytes:
    lines = [f'data: {json.dumps({"choices": [{"delta": {"content": t}}]})}\n\n' for t in texts]
    return ("".join(lines) + 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n').encode()


class Provider:
    """Swaps llm's httpx client for one over a MockTransport, and records the backoff sleeps instead of sleeping."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.calls = 0
        self.sleeps: list[float] = []

        def counted(req: httpx.Request) -> httpx.Response:
            self.calls += 1
            return handler(req)

        real = httpx.AsyncClient
        transport = httpx.MockTransport(counted)

        class Client(real):  # type: ignore[valid-type, misc]
            def __init__(self, *a: Any, **k: Any) -> None:
                super().__init__(*a, transport=transport, **k)

        self._real, self._client = real, Client

    async def _sleep(self, delay: float, cancel: asyncio.Event | None = None) -> bool:
        self.sleeps.append(delay)
        return True

    def __enter__(self) -> "Provider":
        self._old = (llm.httpx.AsyncClient, llm._backoff)
        llm.httpx.AsyncClient = self._client  # type: ignore[misc]
        llm._backoff = self._sleep  # type: ignore[assignment]
        return self

    def __exit__(self, *a: Any) -> None:
        llm.httpx.AsyncClient, llm._backoff = self._old  # type: ignore[misc]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


async def collect(settings: dict[str, Any] = SETTINGS, **kw: Any) -> list[dict[str, Any]]:
    return [ev async for ev in llm.stream_chat(settings, "m", [{"role": "user", "content": "hi"}], **kw)]


def text_of(events: list[dict[str, Any]]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "delta")


class BackoffMath(unittest.TestCase):
    def test_retry_after_seconds_and_date(self) -> None:
        self.assertEqual(llm.parse_retry_after("7"), 7.0)
        self.assertEqual(llm.parse_retry_after(None), None)
        self.assertEqual(llm.parse_retry_after("soon"), None)
        self.assertEqual(llm.parse_retry_after("Wed, 21 Oct 2015 07:28:10 GMT", now=1445412490 - 30), 30.0)
        self.assertEqual(llm.parse_retry_after("Wed, 21 Oct 2015 07:28:10 GMT", now=1445412490 + 30), 0.0)

    def test_delay_grows_is_jittered_and_capped(self) -> None:
        lo = [llm.retry_delay(n, rand=lambda: 0.0) for n in (1, 2, 3, 4)]
        hi = [llm.retry_delay(n, rand=lambda: 1.0) for n in (1, 2, 3, 4)]
        self.assertEqual(lo, [0.5, 1.0, 2.0, 4.0])
        self.assertEqual(hi, [1.0, 2.0, 4.0, 8.0])
        self.assertEqual(llm.retry_delay(40, rand=lambda: 1.0), llm.RETRY_CAP_S)

    def test_retry_after_is_a_floor(self) -> None:
        self.assertEqual(llm.retry_delay(1, 12.0, rand=lambda: 0.0), 12.0)
        self.assertEqual(llm.retry_delay(3, 0.1, rand=lambda: 1.0), 4.0)


class RetryTests(unittest.TestCase):
    def test_rate_limit_then_success_honors_retry_after(self) -> None:
        answers = [httpx.Response(429, headers={"retry-after": "5"}, text="slow down"), httpx.Response(503, text="down"),
                   httpx.Response(200, content=sse("Hel", "lo"))]
        with Provider(lambda req: answers.pop(0)) as p:
            events = run(collect())
        self.assertEqual(text_of(events), "Hello")
        self.assertEqual(p.calls, 3)
        self.assertEqual(len(p.sleeps), 2)
        self.assertGreaterEqual(p.sleeps[0], 5.0)  # Retry-After is a floor
        self.assertLess(p.sleeps[1], 5.0)

    def test_gives_up_with_a_readable_message(self) -> None:
        with Provider(lambda req: httpx.Response(429, text='{"error": {"message": "quota"}}')) as p:
            with self.assertRaises(llm.LLMError) as cm:
                run(collect())
        self.assertEqual(p.calls, 4)  # first try + 3 retries
        msg = str(cm.exception)
        self.assertIn("rate-limiting", msg)
        self.assertIn("Retried 3 times", msg)

    def test_a_client_error_is_not_retried(self) -> None:
        with Provider(lambda req: httpx.Response(401, text='{"error": {"message": "bad key"}}')) as p:
            with self.assertRaises(llm.LLMError) as cm:
                run(collect())
        self.assertEqual(p.calls, 1)
        self.assertIn("API key", str(cm.exception))
        self.assertIn("bad key", str(cm.exception))

    def test_retries_can_be_turned_off(self) -> None:
        with Provider(lambda req: httpx.Response(500, text="boom")) as p:
            with self.assertRaises(llm.LLMError):
                run(collect({**SETTINGS, "llmRetries": 0}))
        self.assertEqual(p.calls, 1)

    def test_connection_errors_are_retried(self) -> None:
        state = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            state["n"] += 1
            if state["n"] < 3:
                raise httpx.ConnectError("refused", request=req)
            return httpx.Response(200, content=sse("ok"))

        with Provider(handler) as p:
            self.assertEqual(text_of(run(collect())), "ok")
        self.assertEqual(p.calls, 3)

    def test_connection_error_forever_says_so(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=req)

        with Provider(handler):
            with self.assertRaises(llm.LLMError) as cm:
                run(collect())
        self.assertIn("Could not reach the model provider", str(cm.exception))
        self.assertIn("Retried 3 times", str(cm.exception))

    def test_never_retries_after_partial_output(self) -> None:
        class Cut(httpx.AsyncByteStream):
            async def __aiter__(self) -> Any:
                yield b'data: {"choices":[{"delta":{"content":"par"}}]}\n\n'
                raise httpx.ReadError("connection reset")

        got: list[dict[str, Any]] = []

        async def go() -> None:
            async for ev in llm.stream_chat(SETTINGS, "m", [{"role": "user", "content": "hi"}]):
                got.append(ev)

        with Provider(lambda req: httpx.Response(200, stream=Cut())) as p:
            with self.assertRaises(llm.LLMError) as cm:
                run(go())
        self.assertEqual(p.calls, 1)
        self.assertEqual(text_of(got), "par")  # what arrived was delivered, and nothing was replayed
        self.assertIn("cut off", str(cm.exception))

    def test_complete_retries_too(self) -> None:
        answers = [httpx.Response(502, text="bad gateway"),
                   httpx.Response(200, json={"choices": [{"message": {"content": "done"}}]})]
        with Provider(lambda req: answers.pop(0)) as p:
            self.assertEqual(run(llm.complete(SETTINGS, "m", [{"role": "user", "content": "x"}])), "done")
        self.assertEqual(p.calls, 2)

    def test_stop_during_backoff_ends_the_wait(self) -> None:
        cancel = asyncio.Event()
        cancel.set()
        self.assertFalse(run(REAL_BACKOFF(30, cancel)))  # a Stop is not held hostage by a 30s backoff
        self.assertTrue(run(REAL_BACKOFF(0.01, asyncio.Event())))


class StreamLimits(unittest.TestCase):
    def test_idle_stream_errors_clearly(self) -> None:
        class Stall(httpx.AsyncByteStream):
            async def __aiter__(self) -> Any:
                yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
                await asyncio.sleep(30)
                yield b"never"

        got: list[dict[str, Any]] = []

        async def go() -> None:
            async for ev in llm.stream_chat({**SETTINGS, "llmIdleSeconds": 0.2}, "m", [{"role": "user", "content": "x"}]):
                got.append(ev)

        t0 = time.monotonic()
        with Provider(lambda req: httpx.Response(200, stream=Stall())):
            with self.assertRaises(llm.LLMError) as cm:
                run(go())
        self.assertLess(time.monotonic() - t0, 5)
        self.assertIn("no data for", str(cm.exception))
        self.assertEqual(text_of(got), "hi")

    def test_deadline_ends_the_stream_as_a_timeout_not_an_error(self) -> None:
        class Slow(httpx.AsyncByteStream):
            async def __aiter__(self) -> Any:
                yield b'data: {"choices":[{"delta":{"content":"so far"}}]}\n\n'
                await asyncio.sleep(30)
                yield b"never"

        async def go() -> list[dict[str, Any]]:
            llm.stream_deadline.set(time.monotonic() + 0.3)
            return await collect()

        with Provider(lambda req: httpx.Response(200, stream=Slow())):
            events = run(go())
        end = events[-1]
        self.assertEqual(end["finish_reason"], "timeout")
        self.assertEqual(end["tool_calls"], [])
        self.assertEqual(text_of(events), "so far")


class Redaction(unittest.TestCase):
    def test_secrets_in_all_the_usual_shapes(self) -> None:
        cases = [
            "Authorization: Bearer abcdef1234567890",
            "headers={'X-Personal-OS-Token': 'tok_abcdefghijkl'}",
            '{"apiKey": "sk-live-abcdefghijklmnop"}',
            "GET https://x.test/v1?key=AIzaSyDUMMYDUMMYDUMMY&q=1",
            "token sk-proj-abcdefghijklmnopqrstuv leaked",
            "stripe sk_live_51H000000000000000000",
            "hook https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX",
            "google ya29.a0AfH6SMCabcdefghijklmnopqrstuvwxyz",
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
            "gh ghp_abcdefghijklmnopqrstuvwxyz0123",
            "googleClientSecret=GOCSPX-abcdefghijkl",
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
        ]
        for c in cases:
            out = logs.redact(c)
            for frag in ("abcdef1234567890", "tok_abcdefghijkl", "sk-live-abcdefghijklmnop", "AIzaSyDUMMY", "sk-proj-abcdefghijklmnopqrstuv",
                         "ghp_abcdefghijklmnopqrstuvwxyz0123", "GOCSPX-abcdefghijkl", "eyJzdWIiOiIx",
                         "sk_live_51H000000000000000000", "hooks.slack.com", "ya29.a0AfH6SMC", "MIIEowIBAAKCAQEA"):
                self.assertNotIn(frag, out, c)
            self.assertIn("[redacted]", out, c)

    def test_ordinary_lines_are_left_alone(self) -> None:
        line = "run 3f2a9c1e proposed send_email (proposal 12) for alice@example.com in 340ms"
        self.assertEqual(logs.redact(line), line)

    def test_registered_literals_are_masked_in_any_shape(self) -> None:
        logs.register_secret("zzzz-weird-shaped-secret")
        self.assertEqual(logs.redact("saw zzzz-weird-shaped-secret here"), "saw [redacted] here")

    def test_filter_scrubs_message_args_and_tracebacks(self) -> None:
        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        h.addFilter(logs.RedactingFilter())
        h.setFormatter(logging.Formatter(logs.FORMAT))
        lg = logging.getLogger("redact-test")
        lg.propagate = False
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
        lg.info("calling with %s", "Authorization: Bearer supersecrettoken123")
        try:
            raise ValueError("bad apiKey=sk-abcdefghijklmnopqrstu")
        except ValueError:
            lg.exception("failed")
        out = buf.getvalue()
        self.assertNotIn("supersecrettoken123", out)
        self.assertNotIn("sk-abcdefghijklmnopqrstu", out)
        self.assertIn("Traceback", out)  # the traceback is kept, only the secret is not

    def test_token_counts_stay_readable_but_url_credentials_go(self) -> None:
        self.assertEqual(logs.redact("prompt_tokens=123 max_tokens: 4096"), "prompt_tokens=123 max_tokens: 4096")
        self.assertIn("[redacted]", logs.redact("token=12345678901"))
        self.assertNotIn("hunter2pw", logs.redact("base http://user:hunter2pw@host:4000/x"))

    def test_setup_logging_writes_a_rotating_redacted_file(self) -> None:
        root = logging.getLogger()
        before = list(root.handlers)
        d = Path(tempfile.mkdtemp(prefix="logs-"))
        try:
            path = logs.setup_logging(d, stream=False)
            self.assertEqual(path, d / "backend.log")
            fh = next(h for h in root.handlers if isinstance(h, RotatingFileHandler) and h not in before)
            self.assertEqual((fh.maxBytes, fh.backupCount), (5 * 1024 * 1024, 5))
            logging.getLogger("personal_os").warning("hello api_key=sk-abcdefghijklmnopqrstu")
            fh.flush()
            text = path.read_text()
            self.assertIn("hello", text)
            self.assertNotIn("sk-abcdefghijklmnopqrstu", text)
            self.assertEqual(logs.setup_logging(d, stream=False), path)  # idempotent
            self.assertEqual(sum(1 for h in root.handlers if isinstance(h, RotatingFileHandler) and h not in before), 1)
            self.assertEqual(logs.tail_lines(path, 1)[-1].split(": ", 1)[1].split(" ")[0], "hello")
        finally:
            for h in list(root.handlers):
                if h not in before:
                    root.removeHandler(h)
                    h.close()


class RetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(Path(tempfile.mkdtemp(prefix="retention-")))
        ToolResults(self.db)  # owns the tool_results table
        self.now = time.time()
        self.old = self.now - 400 * 86400
        self.recent = self.now - 86400

    def one(self, sql: str, *p: Any) -> Any:
        with self.db.tx() as c:
            return c.execute(sql, p).fetchone()[0]

    def test_prunes_bookkeeping_and_keeps_user_content(self) -> None:
        convos = Conversations(self.db)
        conv = convos.create(None, "chat", "m")
        old_msg = convos.add_message(conv["id"], "assistant", "keep me", model="m")
        new_msg = convos.add_message(conv["id"], "assistant", "keep me too", model="m")
        with self.db.tx() as c:
            c.execute("UPDATE messages SET trace='[1]', created_at=? WHERE id=?", (self.old, old_msg["id"]))
            c.execute("UPDATE messages SET trace='[2]', created_at=? WHERE id=?", (self.recent, new_msg["id"]))
            for i, ts in enumerate((self.old, self.recent)):
                c.execute("INSERT INTO usage_log(id, created_at, model, kind) VALUES(?,?,?,?)", (f"u{i}", ts, "m", "chat"))
                c.execute("INSERT INTO tool_results(id, conversation_id, tool, content, total_chars, created_at) VALUES(?,?,?,?,?,?)",
                          (f"t{i}", conv["id"], "x", "payload", 7, ts))
            c.execute("INSERT INTO agent_runs(run_id, conversation_id, started_at, updated_at) VALUES('r1', ?, ?, ?)", (conv["id"], self.old, self.old))
            for cid, status, ts in (("a-old-done", "approved", self.old), ("a-old-pending", "pending", self.old), ("a-new-done", "denied", self.recent)):
                c.execute("INSERT INTO approvals(call_id, run_id, tool, args_digest, status, created_at, decided_at) VALUES(?,?,?,?,?,?,?)",
                          (cid, "r1", "t", "d", status, ts, None if status == "pending" else ts))
            for key, status in (("k-done", "done"), ("k-started", "started")):
                c.execute("INSERT INTO executed_calls(key, run_id, step, tool, args_digest, status, created_at) VALUES(?,?,?,?,?,?,?)",
                          (key, "r1", 1, "t", "d", status, self.old))
        removed = sweep(self.db, {}, now=self.now)
        self.assertEqual(removed["usage_log"], 1)
        self.assertEqual(self.one("SELECT COUNT(*) FROM usage_log"), 1)
        self.assertEqual(removed["tool_results"], 1)
        self.assertEqual(removed["approvals"], 1)
        self.assertEqual(self.one("SELECT COUNT(*) FROM approvals WHERE call_id='a-old-pending'"), 1)  # pending waits forever
        self.assertEqual(self.one("SELECT COUNT(*) FROM approvals WHERE call_id='a-new-done'"), 1)
        self.assertEqual(removed["executed_calls"], 1)
        self.assertEqual(self.one("SELECT COUNT(*) FROM executed_calls WHERE key='k-started'"), 1)  # unknown outcome stays
        # The messages themselves are never deleted; only the old one loses its trace.
        self.assertEqual(self.one("SELECT COUNT(*) FROM messages"), 2)
        self.assertIsNone(self.one("SELECT trace FROM messages WHERE id=?", old_msg["id"]))
        self.assertEqual(self.one("SELECT trace FROM messages WHERE id=?", new_msg["id"]), "[2]")
        self.assertEqual(self.one("SELECT COUNT(*) FROM conversations"), 1)

    def test_thresholds_are_settings(self) -> None:
        with self.db.tx() as c:
            c.execute("INSERT INTO usage_log(id, created_at, model, kind) VALUES('u', ?, 'm', 'chat')", (self.now - 10 * 86400,))
        self.assertEqual(sweep(self.db, {}, now=self.now)["usage_log"], 0)  # default keeps a year
        self.assertEqual(sweep(self.db, {"retainUsageDays": 7}, now=self.now)["usage_log"], 1)

    def test_junk_settings_fall_back_to_defaults(self) -> None:
        with self.db.tx() as c:
            c.execute("INSERT INTO usage_log(id, created_at, model, kind) VALUES('u', ?, 'm', 'chat')", (self.now - 10 * 86400,))
        self.assertEqual(sweep(self.db, {"retainUsageDays": "x", "retainTraceDays": -5, "retainToolResultDays": True}, now=self.now)["usage_log"], 0)


class DiagnosticsRoute(unittest.TestCase):
    def test_report_is_masked_and_carries_versions_and_logs(self) -> None:
        from fastapi.testclient import TestClient

        from personal_os.app import AUTH_TOKEN, app

        c = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
        c.put("/settings", json={"apiKey": "sk-diag-secret-abcdefghijkl", "braveApiKey": "BSA-secret-abcdefghijkl"})
        try:
            r = c.get("/diagnostics")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            blob = r.text
            self.assertNotIn("sk-diag-secret-abcdefghijkl", blob)
            self.assertNotIn("BSA-secret-abcdefghijkl", blob)
            self.assertEqual(body["settings"]["apiKey"], "set")
            self.assertEqual(body["settings"]["googleToken"], "unset")
            self.assertIn("python", body["versions"])
            self.assertIn("os", body["versions"])
            self.assertIn("messages", body["database"]["rows"])
            self.assertIsInstance(body["permissions"], list)
            self.assertEqual(c.post("/maintenance/sweep").status_code, 200)
            self.assertIsNotNone(c.get("/diagnostics").json()["retention_last_sweep"])
        finally:
            c.put("/settings", json={"apiKey": "", "braveApiKey": ""})

    def test_requires_the_token(self) -> None:
        from fastapi.testclient import TestClient

        from personal_os.app import app

        self.assertEqual(TestClient(app).get("/diagnostics").status_code, 401)


class BudgetDeadline(unittest.TestCase):
    def test_arm_deadline_follows_the_wall_clock_budget(self) -> None:
        from personal_os.app import Budget

        b = Budget({"maxRunSeconds": 100})
        b.arm_deadline()
        left = (app_llm.stream_deadline.get() or 0) - time.monotonic()
        self.assertTrue(90 < left <= 100, left)
        Budget({"maxRunSeconds": 0}).arm_deadline()
        self.assertIsNone(app_llm.stream_deadline.get())
        Budget({"maxRunSeconds": 0}).arm_deadline(cap=20)
        self.assertTrue(10 < (app_llm.stream_deadline.get() or 0) - time.monotonic() <= 20)


if __name__ == "__main__":
    unittest.main()
