"""Keeping open descriptors out of child processes.

A child started through uvloop does not get `close_fds`: libuv only closes what it marked itself, so every
descriptor that is inheritable in the backend (the listening socket uvicorn hands a reload worker, the
worker's start-up pipes) lands in the child. A long-lived child then holds the port after the backend is gone.
`seal` marks every inheritable descriptor above stderr non-inheritable; the exec of a child closes those.
"""
from __future__ import annotations

import os
import socket
from collections.abc import Iterable

FD_DIRS = ("/proc/self/fd", "/dev/fd")  # Linux, then macOS and the BSDs
FALLBACK_MAX_FD = 4096


def _open_fds() -> list[int]:
    for d in FD_DIRS:
        try:
            return [int(n) for n in os.listdir(d) if n.isdigit()]
        except OSError:
            continue
    return list(range(3, FALLBACK_MAX_FD))


def seal(keep: Iterable[int] = ()) -> list[int]:
    """Make every inheritable descriptor above stderr non-inheritable; returns the ones changed."""
    skip = set(keep)
    changed: list[int] = []
    for fd in _open_fds():
        if fd <= 2 or fd in skip:
            continue
        try:
            if os.get_inheritable(fd):
                os.set_inheritable(fd, False)
                changed.append(fd)
        except OSError:
            continue  # closed since the listing, or the directory fd of the listing itself
    return changed


def seal_sockets(sockets: Iterable[socket.socket] | None) -> None:
    for sock in sockets or ():
        try:
            sock.set_inheritable(False)
        except OSError:
            pass
