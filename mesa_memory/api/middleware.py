from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


def get_agent_id(request: Request) -> str:
    # Try to get agent_id from JSON body or query params for rate limiting
    # If not found, fallback to remote address
    # For a robust production API, we should extract it from the path or body reliably.
    # We will try to read it from the body but it's async and could consume the stream.
    # MESA v3 API expects agent_id in the payload, but slowapi doesn't easily parse bodies synchronously.
    # Therefore we will rate limit by the API Key header, which uniquely identifies the tenant/user anyway.

    # Actually, we can use the API key directly:
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key

    return get_remote_address(request)


# Create a Limiter object, rate limiting to 60 requests per minute per tenant (identified by API Key)
limiter = Limiter(key_func=get_agent_id, default_limits=["60/minute"])


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

    api_key = request.headers.get("X-API-Key") or request.headers.get("Authorization")
    if not api_key:
        return

    # strip Bearer if present
    agent_id = (
        api_key.replace("Bearer ", "") if api_key.startswith("Bearer ") else api_key
    )

    dao = getattr(request.app.state, "dao", None)
    if dao and daily_limit > 0:
        allowed = await dao.increment_and_check_daily_limit(agent_id, limit=daily_limit)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Daily request limit ({daily_limit}) exceeded for this agent.",
            )
