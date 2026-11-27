import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

_TRUTHY_VALUES = {"1", "true", "yes", "on"}
_RATE_WINDOW_SECONDS = 60.0
_MAX_TRACKED_SUBJECTS = 10_000


def is_rate_limiting_enabled() -> bool:
    """Return whether API rate limiting is enabled (on by default in Docker)."""
    return os.environ.get("MESA_RATE_LIMIT_ENABLED", "false").strip().lower() in _TRUTHY_VALUES


def get_minute_rate_limit() -> int:
    """Return the configured per-minute request limit (default 60)."""
    try:
        return int(os.environ.get("MESA_RATE_LIMIT_PER_MINUTE", "60"))
    except ValueError:
        return 60


class _SlidingMinuteWindow:
    """Bounded, thread-safe in-memory sliding 60-second request counter."""

    def __init__(
        self,
        *,
        window_seconds: float = _RATE_WINDOW_SECONDS,
        max_subjects: int = _MAX_TRACKED_SUBJECTS,
    ) -> None:
        self._window = window_seconds
        self._max_subjects = max_subjects
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = {}

    def _prune(self, bucket: deque[float], cutoff: float) -> None:
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

    def _evict_if_full(self, cutoff: float) -> None:
        if len(self._buckets) < self._max_subjects:
            return
        stale = [
            key
            for key, bucket in self._buckets.items()
            if not bucket or bucket[-1] <= cutoff
        ]
        for key in stale:
            self._buckets.pop(key, None)
        while len(self._buckets) >= self._max_subjects:
            oldest_key = min(
                self._buckets,
                key=lambda key: self._buckets[key][-1] if self._buckets[key] else 0.0,
            )
            self._buckets.pop(oldest_key, None)

    def check_and_record(self, subject: str, limit: int) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` and record if allowed."""
        if limit <= 0:
            return True, 0
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            bucket = self._buckets.get(subject)
            if bucket is None:
                self._evict_if_full(cutoff)
                bucket = deque()
                self._buckets[subject] = bucket
            else:
                self._prune(bucket, cutoff)
            if len(bucket) >= limit:
                retry_after = max(1, int(math.ceil(self._window - (now - bucket[0]))))
                return False, retry_after
            bucket.append(now)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


minute_limiter = _SlidingMinuteWindow()


@dataclass(frozen=True)
class RateLimitSubject:
    """Resolved server-side identity for minute and daily rate limits."""

    value: str
    persistent: bool


def resolve_rate_limit_subject(request: Request) -> RateLimitSubject:
    """Resolve a rate-limit subject without ever deriving it from request input.

    Authenticated memory routes receive ``request.state.principal`` only after
    the API-key dependency has validated the credential.  The principal ID is
    the sole persistent subject.  Requests without that verified context use a
    process-local IP subject for SlowAPI and must never create a daily-limit DB
    record.
    """
    principal = getattr(request.state, "principal", None)
    principal_id = getattr(principal, "principal_id", None)
    if (
        getattr(principal, "status", None) == "active"
        and isinstance(principal_id, str)
        and principal_id
    ):
        return RateLimitSubject(value=principal_id, persistent=True)
    return RateLimitSubject(
        value=f"ip:{get_remote_address(request)}",
        persistent=False,
    )


def get_rate_limit_subject(request: Request) -> str:
    """SlowAPI key function backed by the shared server-side resolver."""
    return resolve_rate_limit_subject(request).value


# Minute and daily limits share ``resolve_rate_limit_subject``. IP subjects
# exist only inside the process-local minute limiter; they are never persisted.
limiter = Limiter(key_func=get_rate_limit_subject, default_limits=["60/minute"])


def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "error": "RATE_LIMITED",
            "detail": str(exc.detail),
            "status_code": 429,
            "retryable": True,
        },
    )


async def check_daily_limit(request: Request) -> None:
    """Dependency to enforce a daily limit of requests using SQLite via MemoryDAO."""
    # Exclude non-API endpoints from the daily cost limit (like /health, /metrics)
    if request.url.path in ("/health", "/health/init", "/metrics"):
        return

    try:
        daily_limit = int(os.environ.get("MESA_DAILY_REQUEST_LIMIT", "10000"))
    except ValueError:
        daily_limit = 10000

    subject = resolve_rate_limit_subject(request)
    if not subject.persistent:
        return

    dao = getattr(request.app.state, "dao", None)
    if dao and daily_limit > 0:
        allowed = await dao.increment_and_check_daily_limit(
            subject.value, limit=daily_limit
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Daily request limit ({daily_limit}) exceeded for this principal.",
                headers={"Retry-After": "3600"},
            )


async def check_rate_limits(request: Request) -> None:
    """Enforce per-minute and daily limits on authenticated API routes when enabled."""
    if not is_rate_limiting_enabled():
        return
    if request.url.path in ("/health", "/health/init", "/metrics"):
        return

    per_minute = get_minute_rate_limit()
    subject = resolve_rate_limit_subject(request)
    allowed, retry_after = minute_limiter.check_and_record(subject.value, per_minute)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({per_minute} requests/minute).",
            headers={"Retry-After": str(retry_after)},
        )
    await check_daily_limit(request)
