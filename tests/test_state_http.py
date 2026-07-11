"""retry_call — the shared state-fetcher retry wrapper (§4.4)."""
from __future__ import annotations

import urllib.error

import pytest

from scripts.state_http import retry_call


def test_returns_on_first_success():
    calls = []
    assert retry_call(lambda: (calls.append(1), "ok")[1], sleep=lambda *_: None) == "ok"
    assert len(calls) == 1


def test_retries_transient_then_succeeds():
    n = {"i": 0}
    def fn():
        n["i"] += 1
        if n["i"] < 3:
            raise urllib.error.URLError("connection reset")
        return "ok"
    assert retry_call(fn, tries=3, sleep=lambda *_: None) == "ok"
    assert n["i"] == 3


def test_raises_after_exhausting_tries():
    def fn():
        raise TimeoutError("slow")
    with pytest.raises(TimeoutError):
        retry_call(fn, tries=3, sleep=lambda *_: None)


def test_permanent_4xx_not_retried():
    calls = []
    def fn():
        calls.append(1)
        raise urllib.error.HTTPError("u", 404, "not found", {}, None)
    with pytest.raises(urllib.error.HTTPError):
        retry_call(fn, tries=3, sleep=lambda *_: None)
    assert len(calls) == 1  # not retried


def test_429_is_retried():
    n = {"i": 0}
    def fn():
        n["i"] += 1
        if n["i"] < 2:
            raise urllib.error.HTTPError("u", 429, "rate", {}, None)
        return "ok"
    assert retry_call(fn, tries=3, sleep=lambda *_: None) == "ok"
    assert n["i"] == 2
