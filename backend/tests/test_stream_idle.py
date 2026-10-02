"""A provider that accepts the request and then goes silent must not hold a reply open forever."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="idletest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import llm  # noqa: E402


async def _run() -> str:
    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(65536)
        writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n\r\n")
        await writer.drain()
        await asyncio.sleep(30)  # never sends a token

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    llm.STREAM_IDLE_S = 0.5
    try:
        async for _ in llm.stream_chat({"baseUrl": f"http://127.0.0.1:{port}", "apiKey": "x"}, "m",
                                       [{"role": "user", "content": "hi"}], cancel=asyncio.Event()):
            pass
    except llm.LLMError as e:
        return str(e)
    finally:
        server.close()
    return ""


def test_silent_stream_gives_up_with_an_error() -> None:
    msg = asyncio.run(asyncio.wait_for(_run(), 15))
    assert "sent nothing" in msg


if __name__ == "__main__":
    test_silent_stream_gives_up_with_an_error()
    print("ok")
