from dataclasses import dataclass

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


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
        content={"error": "Rate limit exceeded", "detail": str(exc.detail)},
    )


async def check_daily_limit(request: Request) -> None:
    """Dependency to enforce a daily limit of requests using SQLite via MemoryDAO."""
    # Exclude non-API endpoints from the daily cost limit (like /health, /metrics)
    if request.url.path in ("/health", "/health/init", "/metrics", "/v3/health"):
        return

    import os

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
            )
