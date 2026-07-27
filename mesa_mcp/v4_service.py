"""MCP-facing adapter for the MESA V4 HTTP application service."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from mesa_client.client import AsyncMesaV4Client, MesaAPIError, MesaNetworkError

from .configuration import MCPSettings
from .errors import MCPError


class MesaHttpV4Service:
    """Use MESA's V4 public API; this class never imports a storage backend."""

    def __init__(self, settings: MCPSettings):
        self._settings = settings
        self._session_cache: dict[str, str] = {}
        self._http_client = AsyncMesaV4Client(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=8.0,
            max_retries=0,
        )

    async def close(self) -> None:
        await self._http_client.aclose()

    async def health(self) -> dict[str, Any]:
        try:
            return await self._http_client._request("GET", "/health")
        except Exception as exc:
            raise _map_exception(exc) from exc

    async def _get_session_id(
        self,
        client: AsyncMesaV4Client,
        dataset_id: str,
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        actor_id: str | None = None,
    ) -> str:
        tenant_id = tenant_id or self._settings.default_tenant_id
        workspace_id = workspace_id or self._settings.default_workspace_id
        actor_id = actor_id or self._settings.actor_id
        cache_key = f"{tenant_id}:{workspace_id}:{actor_id}:{dataset_id}"
        if cache_key in self._session_cache:
            return self._session_cache[cache_key]

        try:
            resp = await client.start_session(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_ids=[dataset_id],
                agent_id=actor_id,
            )
            session_id = resp["session_id"]
            self._session_cache[cache_key] = session_id
            return session_id
        except Exception as exc:
            raise _map_exception(exc) from exc

    async def v4_remember(self, **kwargs: Any) -> dict[str, Any]:
        dataset_id = kwargs.get("dataset_id") or self._settings.default_dataset_id
        tenant_id = kwargs.get("tenant_id") or self._settings.default_tenant_id
        workspace_id = kwargs.get("workspace_id") or self._settings.default_workspace_id
        actor_id = kwargs.get("actor_id") or self._settings.actor_id
        document_id = kwargs.get("document_id") or f"doc_{uuid.uuid4().hex[:8]}"
        content = kwargs["content"]

        client = self._http_client
        session_id = await self._get_session_id(
            client,
            dataset_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
        )

        try:
            await client.create_document(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                document_id=document_id,
                title=kwargs.get("title", f"Memory {document_id}"),
            )
        except MesaAPIError as exc:
            if exc.status_code != 409:
                raise _map_exception(exc) from exc
        try:
            return await client.insert(
                session_id=session_id,
                dataset_id=dataset_id,
                document_id=document_id,
                revision_id="rev_1",
                chunk_id="chunk_1",
                title=kwargs.get("title", f"Memory {document_id}"),
                source_ref=kwargs.get("source_ref", "mcp_tool"),
                content=content,
                metadata=kwargs.get("metadata", {}),
                idempotency_key=kwargs.get("idempotency_key"),
            )
        except Exception as exc:
            raise _map_exception(exc) from exc

    async def v4_recall(self, **kwargs: Any) -> list[dict[str, Any]]:
        dataset_id = kwargs.get("dataset_id") or self._settings.default_dataset_id
        tenant_id = kwargs.get("tenant_id") or self._settings.default_tenant_id
        workspace_id = kwargs.get("workspace_id") or self._settings.default_workspace_id
        actor_id = kwargs.get("actor_id") or self._settings.actor_id

        client = self._http_client
        session_id = await self._get_session_id(
            client,
            dataset_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
        )
        try:
            resp = await client.search(
                session_id=session_id,
                query=kwargs["query"],
                dataset_ids=[dataset_id],
                limit=kwargs.get("limit", self._settings.search_default_limit),
            )
            return [_typed_result(item) for item in resp.get("results", [])]
        except Exception as exc:
            raise _map_exception(exc) from exc

    async def v4_improve(self, **kwargs: Any) -> dict[str, Any]:
        # Improve is essentially creating a new revision
        dataset_id = kwargs.get("dataset_id") or self._settings.default_dataset_id
        tenant_id = kwargs.get("tenant_id") or self._settings.default_tenant_id
        workspace_id = kwargs.get("workspace_id") or self._settings.default_workspace_id
        document_id = kwargs["document_id"]
        content = kwargs["content"]

        client = self._http_client
        try:
            return await client.create_revision(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                document_id=document_id,
                revision_id=f"rev_{uuid.uuid4().hex[:8]}",
                revision_number=kwargs.get("revision_number", 2),
                content_sha256=hashlib.sha256(content.encode()).hexdigest(),
            )
        except Exception as exc:
            raise _map_exception(exc) from exc

    async def v4_forget(self, **kwargs: Any) -> dict[str, Any]:
        dataset_id = kwargs.get("dataset_id") or self._settings.default_dataset_id
        tenant_id = kwargs.get("tenant_id") or self._settings.default_tenant_id
        workspace_id = kwargs.get("workspace_id") or self._settings.default_workspace_id
        document_id = kwargs["document_id"]

        client = self._http_client
        try:
            return await client.purge_document(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                document_id=document_id,
            )
        except Exception as exc:
            raise _map_exception(exc) from exc


def _typed_result(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") or {}
    return {
        "memory_id": item.get("memory_id") or item.get("chunk_id"),
        "document_id": item.get("document_id"),
        "chunk_id": item.get("chunk_id"),
        "content": item.get("content"),
        "memory_type": metadata.get("memory_type", "unknown"),
        "status": item.get("status", "active"),
        "score": item.get("score", 0.0),
        "provenance": item.get("provenance", {}),
    }


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
