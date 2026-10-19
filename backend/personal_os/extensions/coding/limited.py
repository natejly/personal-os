"""Work that can backtrack without bound (a regular expression an agent wrote, a schema `pattern`) runs in a child
interpreter that is killed when the time is up: Python's `re` cannot be interrupted in this process, and a thread would
still hold the event loop's GIL."""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

LIMIT_S = 5.0


async def run_limited(code: str, payload: Any, timeout: float = LIMIT_S) -> Any | None:
    """The JSON the child prints after `code` read `payload` (JSON on stdin), or None if it ran past `timeout`."""
    proc = await asyncio.create_subprocess_exec(sys.executable, "-I", "-c", code, stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(json.dumps(payload).encode()), timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    return json.loads(out)
