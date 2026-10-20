"""Optional API-key protection and bounded submission rate limiting."""

from __future__ import annotations

import hmac
import threading
import time
from collections import deque
from typing import Callable

from fastapi import Header, HTTPException, Request

from app.config import get_settings

MAX_TRACKED_CLIENTS = 10_000
_PUBLIC_PATHS = frozenset({"/health"})


def api_key_matches(provided: str | None, expected: str) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(
        provided.encode("utf-8"),
        expected.encode("utf-8"),
    )


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> None:
    expected = get_settings().service_api_key
    if not expected or request.url.path in _PUBLIC_PATHS:
        return
    if not api_key_matches(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


class SlidingWindowLimiter:
    def __init__(
        self,
        limit: int,
        window: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1 or window <= 0:
            raise ValueError("Rate limit and window must be positive.")
        self.limit = limit
        self.window = window
        self._clock = clock
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            events = self._events.get(key)
            if events is None:
                if len(self._events) >= MAX_TRACKED_CLIENTS:
                    self._prune(now)
                    if len(self._events) >= MAX_TRACKED_CLIENTS:
                        return False
                events = self._events.setdefault(key, deque())
            while events and now - events[0] >= self.window:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True

    def _prune(self, now: float) -> None:
        stale = [
            key
            for key, events in self._events.items()
            if not events or now - events[-1] >= self.window
        ]
        for key in stale:
            del self._events[key]


_limiter: SlidingWindowLimiter | None = None
_limiter_lock = threading.Lock()


def limit_submissions(request: Request) -> None:
    global _limiter
    limit = get_settings().submission_rate_limit_per_minute
    if limit <= 0:
        return
    with _limiter_lock:
        if _limiter is None or _limiter.limit != limit:
            _limiter = SlidingWindowLimiter(limit)
        limiter = _limiter
    client_key = request.client.host if request.client else "unknown"
    if not limiter.allow(client_key):
        raise HTTPException(
            status_code=429,
            detail="Too many submissions; retry later.",
        )
