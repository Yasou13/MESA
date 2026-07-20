"""Network-free coverage for SDK error and retry contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from mesa_client.client import (
    MesaNetworkError,
    _async_retry,
    _parse_api_error,
    _sync_retry,
)


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
