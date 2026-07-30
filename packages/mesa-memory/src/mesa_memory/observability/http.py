"""Pure ASGI request logging and correlation middleware."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mesa_memory.observability.metrics import PROM_HTTP_REQUESTS

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_TRACEPARENT_RE = re.compile(
    r"^(?!ff)[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$"
)
_QUIET_ROUTES = {"/health", "/health/init", "/metrics", "/v3/health"}


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return bytes(value).decode("latin-1")
    return None


def _request_id(scope: Scope) -> str:
    candidate = _header(scope, b"x-request-id")
    if candidate and _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


def _trace_id(scope: Scope) -> str | None:
    candidate = _header(scope, b"traceparent")
    if not candidate:
        return None
    match = _TRACEPARENT_RE.fullmatch(candidate.lower())
    return match.group(1) if match else None


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if path else "unmatched"


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.logger = structlog.get_logger("MESA_HTTP")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope)
        trace_id = _trace_id(scope)
        structlog.contextvars.clear_contextvars()
        context: dict[str, Any] = {
            "request_id": request_id,
            "method": scope.get("method", ""),
        }
        if trace_id:
            context["trace_id"] = trace_id
        structlog.contextvars.bind_contextvars(**context)
        scope.setdefault("state", {})["request_id"] = request_id

        status_code = 500
        started = time.monotonic()
        exception_type: str | None = None

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            exception_type = type(exc).__name__
            raise
        finally:
            route = _route_template(scope)
            duration_ms = round((time.monotonic() - started) * 1000, 3)
            failed = status_code >= 400 or exception_type is not None
            event = "http_request_failed" if failed else "http_request_completed"
            fields: dict[str, Any] = {
                "route": route,
                "status_code": status_code,
                "duration_ms": duration_ms,
            }
            if exception_type:
                fields["exception_type"] = exception_type

            if status_code < 400 and route in _QUIET_ROUTES:
                self.logger.debug(event, **fields)
            elif status_code >= 500:
                self.logger.error(event, **fields)
            elif status_code >= 400:
                self.logger.warning(event, **fields)
            else:
                self.logger.info(event, **fields)

            if route != "/metrics":
                PROM_HTTP_REQUESTS.labels(
                    method=str(scope.get("method", "")),
                    endpoint=route,
                    status=str(status_code),
                ).inc()
            structlog.contextvars.clear_contextvars()
