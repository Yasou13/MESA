"""HTTP request correlation and bounded route-label contracts."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import pytest
import structlog
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from mesa_memory.observability.http import RequestLoggingMiddleware
from mesa_memory.observability.logger import setup_logging
from mesa_memory.observability.metrics import PROM_HTTP_REQUESTS


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.exception_handler(Exception)
    async def unhandled(request, exc):
        request.state.exception_type = type(exc).__name__
        return PlainTextResponse(
            "Internal Server Error",
            status_code=500,
            headers={"X-Request-ID": request.state.request_id},
        )

    @app.get("/items/{item_id}")
    async def item(item_id: str):
        return {"item_id": item_id}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/unavailable")
    async def unavailable():
        raise HTTPException(status_code=503, detail="unavailable")

    @app.get("/crash")
    async def crash():
        raise RuntimeError("sensitive exception message")

    return app


@dataclass(frozen=True)
class _Response:
    status_code: int
    headers: dict[str, str]
    body: str


async def _request(
    app: FastAPI,
    path: str,
    *,
    request_id: str | None = None,
    traceparent: str | None = None,
    raises: type[Exception] | None = None,
) -> _Response:
    request_headers = []
    if request_id is not None:
        request_headers.append((b"x-request-id", request_id.encode("latin-1")))
    if traceparent is not None:
        request_headers.append((b"traceparent", traceparent.encode("ascii")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": request_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "state": {},
    }
    sent: list[dict] = []
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    try:
        await app(scope, receive, send)
    except Exception as exc:
        if raises is None or not isinstance(exc, raises):
            raise
    start = next(
        message for message in sent if message["type"] == "http.response.start"
    )
    headers = {
        key.decode("latin-1"): value.decode("latin-1")
        for key, value in start.get("headers", [])
    }
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    ).decode("utf-8")
    return _Response(status_code=start["status"], headers=headers, body=body)


def _records(capsys) -> list[dict]:
    return [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("{")
    ]


@pytest.mark.asyncio
async def test_request_id_is_accepted_or_replaced_and_route_is_templated(capsys):
    setup_logging(role="api")
    app = _app()
    accepted = await _request(
        app,
        "/items/123",
        request_id="req-123",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
    )
    replaced = await _request(app, "/items/456", request_id="bad id!")

    assert accepted.headers["x-request-id"] == "req-123"
    assert replaced.headers["x-request-id"] != "bad id!"
    records = [r for r in _records(capsys) if r["event"] == "http_request_completed"]
    assert [r["route"] for r in records] == ["/items/{item_id}", "/items/{item_id}"]
    assert [r["request_id"] for r in records] == [
        "req-123",
        replaced.headers["x-request-id"],
    ]
    assert all("duration_ms" in r and r["status_code"] == 200 for r in records)
    assert records[0]["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    endpoints = {
        sample.labels.get("endpoint")
        for metric in PROM_HTTP_REQUESTS.collect()
        for sample in metric.samples
    }
    assert "/items/{item_id}" in endpoints
    assert "/items/123" not in endpoints


@pytest.mark.asyncio
async def test_health_success_is_quiet_but_failure_is_logged(capsys):
    setup_logging(role="api")
    app = _app()
    health = await _request(app, "/health")
    missing = await _request(app, "/missing")
    failed = await _request(app, "/unavailable")

    assert health.status_code == 200
    assert missing.status_code == 404
    assert failed.status_code == 503
    records = _records(capsys)
    assert not any(r.get("route") == "/health" for r in records)
    failure = next(r for r in records if r.get("route") == "/unavailable")
    not_found = next(r for r in records if r.get("status_code") == 404)
    assert not_found["level"] == "warning"
    assert not_found["route"] == "unmatched"
    assert failure["event"] == "http_request_failed"
    assert failure["level"] == "error"
    assert failed.headers["x-request-id"] == failure["request_id"]


@pytest.mark.asyncio
async def test_unhandled_500_preserves_generic_body_contract_and_request_id(capsys):
    setup_logging(role="api")
    response = await _request(_app(), "/crash", raises=RuntimeError)

    assert response.status_code == 500
    assert response.body == "Internal Server Error"
    records = _records(capsys)
    failure = next(r for r in records if r.get("route") == "/crash")
    assert response.headers["x-request-id"] == failure["request_id"]
    assert failure["exception_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_request_context_is_isolated_across_concurrent_requests(capsys):
    setup_logging(role="api")
    app = _app()
    responses = await asyncio.gather(
        _request(app, "/items/0", request_id="req-a"),
        _request(app, "/items/1", request_id="req-b"),
    )
    assert [response.status_code for response in responses] == [200, 200]

    records = [
        r for r in _records(capsys) if r.get("event") == "http_request_completed"
    ]
    assert {r["request_id"] for r in records} == {"req-a", "req-b"}
    assert structlog.contextvars.get_contextvars() == {}
