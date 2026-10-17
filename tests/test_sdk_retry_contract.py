"""Network-free coverage for SDK error and retry contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from mesa_client.client import (
    MesaAPIError,
    MesaNetworkError,
    MesaV4Client,
    _async_retry,
    _parse_api_error,
    _sync_retry,
)


def test_wait_allows_slow_processing_without_exhausting_minute_request_budget(monkeypatch):
    elapsed = 0.0
    calls = 0

    def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds

    def status(_mutation_id):
        nonlocal calls
        calls += 1
        if calls > 60:
            raise MesaAPIError(429, "RATE_LIMITED", "minute budget exceeded")
        return {"state": "COMMITTED" if elapsed >= 30 else "PENDING"}

    monkeypatch.setattr("mesa_client.client.time.monotonic", lambda: elapsed)
    monkeypatch.setattr("mesa_client.client.time.sleep", sleep)
    with MesaV4Client() as client:
        monkeypatch.setattr(client, "status", status)
        assert client.wait_until_committed("mutation-a")["state"] == "COMMITTED"


def _request(method: str = "GET") -> httpx.Request:
    return httpx.Request(method, "https://mesa.test/v3/memory/search")


def test_parse_api_error_preserves_structured_server_response() -> None:
    response = httpx.Response(
        403,
        json={"status_code": 403, "error": "forbidden", "detail": "tenant denied"},
        request=_request(),
    )

    error = _parse_api_error(response)

    assert error.status_code == 403
    assert error.error == "forbidden"
    assert error.detail == "tenant denied"


def test_parse_api_error_falls_back_for_nonstandard_payload() -> None:
    response = httpx.Response(502, text="upstream unavailable", request=_request())

    error = _parse_api_error(response)

    assert error.status_code == 502
    assert error.error == "UnknownError"
    assert error.detail == "upstream unavailable"


def test_sync_retry_retries_safe_connect_failure() -> None:
    response = httpx.Response(200, json={"ok": True}, request=_request())
    operation = Mock(
        side_effect=[httpx.ConnectError("offline", request=_request()), response]
    )

    with patch("mesa_client.client.time.sleep") as sleep:
        assert _sync_retry(operation, max_retries=2, base_delay=0.01) is response

    assert operation.call_count == 2
    sleep.assert_called_once_with(0.01)


def test_sync_retry_refuses_unsafe_post_retry() -> None:
    operation = Mock(
        side_effect=httpx.ReadTimeout("uncertain write", request=_request("POST"))
    )

    with pytest.raises(MesaNetworkError, match="Refusing to retry non-idempotent POST"):
        _sync_retry(operation, max_retries=2)


@pytest.mark.asyncio
async def test_async_retry_retries_safe_connect_failure() -> None:
    response = httpx.Response(200, json={"ok": True}, request=_request())
    operation = AsyncMock(
        side_effect=[httpx.ConnectTimeout("offline", request=_request()), response]
    )

    with patch("mesa_client.client.asyncio.sleep", new_callable=AsyncMock) as sleep:
        assert await _async_retry(operation, max_retries=2, base_delay=0.01) is response

    assert operation.await_count == 2
    sleep.assert_awaited_once_with(0.01)


@pytest.mark.asyncio
async def test_async_retry_reports_exhausted_network_budget() -> None:
    operation = AsyncMock(side_effect=httpx.ConnectError("offline", request=_request()))

    with patch("mesa_client.client.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(MesaNetworkError, match=r"Max retries \(2\) exceeded"):
            await _async_retry(operation, max_retries=2, base_delay=0.01)

    assert operation.await_count == 2
