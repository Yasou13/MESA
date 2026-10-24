"""MCP-facing adapter for the existing MESA HTTP application service."""

from __future__ import annotations

from typing import Any

import httpx

from mesa_client.client import AsyncMesaClient, MesaAPIError, MesaNetworkError

from .configuration import MCPSettings
from .errors import MCPError


class MesaHttpMemoryService:
    """Use MESA's public API; this class never imports a storage backend."""

    def __init__(self, settings: MCPSettings):
        self._settings = settings
        # The MCP process is long lived.  Keep one client for its lifetime so
        # keep-alive connections and DNS/socket state are reused between tool
        # calls instead of creating a new TCP connection per request.
        self._http_client = AsyncMesaClient(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=httpx.Timeout(connect=2.0, read=8.0, write=8.0, pool=2.0),
            max_retries=0,
        )

    async def close(self) -> None:
        await self._http_client.aclose()

    async def health(self) -> dict[str, Any]:
        try:
            response = await self._http_client._request("GET", "/health")
        except Exception as exc:  # mapped below to keep protocol errors stable
            raise _map_exception(exc) from exc
        return response


def _map_exception(exc: Exception) -> MCPError:
    if isinstance(exc, MCPError):
        return exc
    if isinstance(exc, MesaNetworkError):
        return MCPError(
            "BACKEND_UNAVAILABLE", "MESA service is unavailable", retryable=True
        )
    if isinstance(exc, MesaAPIError):
        if exc.status_code in {400, 422}:
            return MCPError("INVALID_ARGUMENT", "MESA rejected the request")
        if exc.status_code in {401, 403}:
            return MCPError(
                "ACCESS_DENIED", "MESA denied access to the requested scope"
            )
        if exc.status_code == 404:
            return MCPError("NOT_FOUND", "memory was not found")
        if exc.status_code in {408, 429, 503, 504}:
            return MCPError(
                "BACKEND_UNAVAILABLE",
                "MESA service is temporarily unavailable",
                retryable=True,
            )
    return MCPError("INTERNAL_ERROR", "MESA operation failed")
