"""Shared retry wrapper for the state portal fetchers (§4.4).

The API/HTTP state fetchers (fetch_ny / fetch_wa / fetch_az / fetch_fl) used bare
`urlopen` / `opener.open` with zero retry, so one transient 500 / connection
reset cost that state its whole monthly refresh. `retry_call` runs a network
thunk with a small exponential backoff, retrying transient failures (URLError,
timeouts, 5xx, 429) and failing fast on a permanent 4xx (so a bad request isn't
retried pointlessly).
"""
from __future__ import annotations

import time
from typing import Callable, TypeVar
from urllib.error import HTTPError, URLError

T = TypeVar("T")


def retry_call(
    fn: Callable[[], T],
    *,
    tries: int = 3,
    base_delay: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call `fn()`, retrying transient network failures up to `tries` times.

    Permanent client errors (HTTP 4xx except 429) are re-raised immediately —
    retrying a bad request just wastes time.
    """
    last: Exception | None = None
    for attempt in range(tries):
        try:
            return fn()
        except HTTPError as e:
            if e.code is not None and 400 <= e.code < 500 and e.code != 429:
                raise  # permanent — do not retry
            last = e
        except (URLError, TimeoutError, OSError) as e:
            last = e
        if attempt < tries - 1:
            sleep(base_delay * (2 ** attempt))
    assert last is not None
    raise last
