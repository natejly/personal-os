import asyncio

from personal_os import thinking_summary as ts


def test_clean():
    assert ts.clean("<think>x</think>\n\"Checking calendar for conflicts.\"\nmore") == "Checking calendar for conflicts"


def test_throttle_and_stored():
    calls = []

    async def fake(cfg, model, chunk):
        calls.append(chunk)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return f"line {len(calls)}"

    async def go():
        t = ts.ThinkingSummary({}, "m", fake)
        t.add("a" * 100)
        assert not calls                       # too little text
        t.add("b" * 600)
        t.add("c" * 700)                       # one call in flight: no second
        await asyncio.sleep(0)
        assert len(calls) == 1
        assert t.take() == ["line 1"]
        t.add("d" * 700)                       # inside the gap: held back
        await asyncio.sleep(0)
        assert len(calls) == 1
        t._last = float("-inf")
        t.add("e")
        await asyncio.sleep(0)
        assert t.take() == [] and len(calls) == 2   # failure swallowed
        assert t.stored() == "line 1"
        t.reset()
        assert t.stored() is None

    asyncio.run(go())
