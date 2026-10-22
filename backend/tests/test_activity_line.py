import asyncio

from personal_os import activity_line as al


def test_scrub():
    out = al.scrub({"query": "weather", "api_key": "abc", "Authorization": "Bearer x", "nested": {"cookie": "c", "ok": "y" * 5},
                    "blob": "A" * 40, "path": "/Users/me/some/where/file.txt", "n": 3})
    assert out["query"] == "weather" and out["api_key"] == "[hidden]" and out["Authorization"] == "[hidden]"
    assert out["nested"] == {"cookie": "[hidden]", "ok": "yyyyy"}
    assert out["blob"] == "[opaque]" and out["path"].startswith("/Users") and out["n"] == 3


def test_due():
    ok = dict(dirty=True, watching=True, enabled=True, failures=0, inflight=False)
    assert al.due(10, 0, **ok)
    assert not al.due(3, 0, **ok)                       # inside the gap
    assert not al.due(10, 0, **{**ok, "dirty": False})  # nothing new
    assert not al.due(10, 0, **{**ok, "watching": False})
    assert not al.due(10, 0, **{**ok, "failures": 2})
    assert not al.due(10, 0, **{**ok, "inflight": True})


class FakeRun:
    watchers = 1

    def __init__(self):
        self.sent = []

    def emit_transient(self, event, data):
        self.sent.append((event, data))


def test_stale_result_dropped():
    async def go():
        run, calls = FakeRun(), []
        gate = asyncio.Event()

        async def fn(cfg, model, tools, reasoning):
            calls.append(tools)
            await gate.wait()
            return "Doing it…"

        a = al.ActivityLine(run, {}, "m", fn, clock=lambda: 100.0)
        a.note_tool("m1", "web_search", {"query": "x", "token": "t"})
        await asyncio.sleep(0)
        a.note_tool("m1", "fetch_url", {})  # a newer step: the in-flight result is stale
        gate.set()
        await asyncio.sleep(0.01)
        assert run.sent == [] and calls[0] == [("web_search", {"query": "x", "token": "[hidden]"})]
        a.stop()
    asyncio.run(go())


def test_emits_then_stops_after_two_failures():
    async def go():
        run, n = FakeRun(), iter(range(0, 1000, 10))

        async def ok(*x):
            return "Searching…"

        a = al.ActivityLine(run, {}, "m", ok, clock=lambda: next(n))
        a.note_tool("m1", "web_search", {})
        await asyncio.sleep(0.01)
        assert run.sent == [("activity", {"message_id": "m1", "text": "Searching…"})]

        async def boom(*x):
            raise RuntimeError("x")

        b = al.ActivityLine(run, {}, "m", boom, clock=lambda: next(n))
        for _ in range(4):
            b.note_tool("m1", "t", {})
            await asyncio.sleep(0.01)
        assert b._failures == 2
    asyncio.run(go())
