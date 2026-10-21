"""How the backend is served: uvicorn's settings, and a server that tells open event streams to end on the exit signal."""
from __future__ import annotations

import sys
import types
from typing import Any

import uvicorn
from uvicorn.supervisors import ChangeReload

STARTUP_FAILURE = 3  # uvicorn's own exit code for a server that never came up

from . import runs

# Seconds an open connection gets after the exit signal before uvicorn cancels it. The event streams end on their own
# as soon as the signal arrives; this bounds anything else still holding a response open (a slow upload, a stuck client).
GRACEFUL_SHUTDOWN_S = 5


def uvicorn_kwargs(host: str, port: int, reload: bool) -> dict[str, Any]:
    # log_config=None: uvicorn's own dictConfig would give its loggers private handlers and cut them off from the
    # redacting file handler on the root logger.
    return {"app": "personal_os.app:app", "host": host, "port": port, "reload": reload, "log_level": "info",
            "log_config": None, "timeout_graceful_shutdown": GRACEFUL_SHUTDOWN_S}


class Server(uvicorn.Server):
    def handle_exit(self, sig: int, frame: types.FrameType | None) -> None:
        # Before uvicorn starts waiting for connections: a follow-forever stream would otherwise be what it waits on.
        runs.begin_shutdown()
        super().handle_exit(sig, frame)


def serve(**kwargs: Any) -> None:
    """uvicorn.run, with the server class swapped for ours."""
    config = uvicorn.Config(**kwargs)
    server = Server(config)
    try:
        if config.should_reload:
            ChangeReload(config, target=server.run, sockets=[config.bind_socket()]).run()
        else:
            config.load_app()
            server.run()
    except KeyboardInterrupt:
        return
    if not server.started and not config.should_reload:
        sys.exit(STARTUP_FAILURE)
