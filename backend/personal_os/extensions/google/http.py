"""Google calls from HTTP routes: the failures a route should answer with, whichever extension owns the route."""
from __future__ import annotations

import contextlib
from typing import Any, Callable

from fastapi import HTTPException

from ... import cache as google_cache
from .client import Google, GoogleNotConnected, http_failure, json_safe


def http_error(google: Google, e: Exception) -> HTTPException:
    if isinstance(c := google.classify_error(e), GoogleNotConnected):
        return HTTPException(409, str(c))
    return HTTPException(*http_failure(e))


def gcall(google: Google, fn: Callable[..., Any], *args: Any, refresh: bool = False) -> Any:
    """Call a Google method and map its failures onto HTTP.

    `refresh=True` serves the call from Google rather than the read cache - what a
    user-initiated reload should do.
    """
    try:
        with google_cache.bypass() if refresh else contextlib.nullcontext():
            return json_safe(fn(*args))
    except GoogleNotConnected as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise http_error(google, e) from e
