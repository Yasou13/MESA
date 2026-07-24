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

    async def _get_session_id(self, client: AsyncMesaV4Client, dataset_id: str) -> str:
        cache_key = f"{self._settings.actor_id}:{dataset_id}"
        if cache_key in self._session_cache:
            return self._session_cache[cache_key]

        try:
            resp = await client.start_session(
                tenant_id=self._settings.default_tenant_id,
                workspace_id=self._settings.default_workspace_id,
                dataset_ids=[dataset_id],
                agent_id=self._settings.actor_id,
            )
            session_id = resp["session_id"]
            self._session_cache[cache_key] = session_id
            return session_id
        except Exception as exc:
            raise _map_exception(exc) from exc

    async def v4_remember(self, **kwargs: Any) -> dict[str, Any]:
        dataset_id = kwargs.get("dataset_id") or self._settings.default_dataset_id
        document_id = kwargs.get("document_id") or f"doc_{uuid.uuid4().hex[:8]}"
        content = kwargs["content"]

        async with self._client() as client:
            session_id = await self._get_session_id(client, dataset_id)

            # For simplicity, we create the document if it doesn't exist (assuming idempotent or ignoring errors for now)
            try:
                await client.create_document(
                    tenant_id=self._settings.default_tenant_id,
                    workspace_id=self._settings.default_workspace_id,
                    dataset_id=dataset_id,
                    document_id=document_id,
                    title=kwargs.get("title", f"Memory {document_id}"),
                )
            except Exception:
                pass  # Ignore if it exists

            try:
                resp = await client.insert(
                    session_id=session_id,
                    dataset_id=dataset_id,
                    document_id=document_id,
                    revision_id="rev_1",
                    chunk_id="chunk_1",
                    title=kwargs.get("title", f"Memory {document_id}"),
                    source_ref=kwargs.get("source_ref", "mcp_tool"),
                    content=content,
                    metadata=kwargs.get("metadata", {}),
                )
                return resp
            except Exception as exc:
                raise _map_exception(exc) from exc

    async def v4_recall(self, **kwargs: Any) -> list[dict[str, Any]]:
        dataset_id = kwargs.get("dataset_id") or self._settings.default_dataset_id

        async with self._client() as client:
            session_id = await self._get_session_id(client, dataset_id)

            try:
                resp = await client.search(
                    session_id=session_id,
                    query=kwargs["query"],
                    dataset_ids=[dataset_id],
                    limit=kwargs.get("limit", self._settings.search_default_limit),
                )

                results = []
                for item in resp.get("results", []):
                    results.append(
                        {
                            "document_id": item.get("document_id"),
                            "chunk_id": item.get("chunk_id"),
                            "content": item.get("content"),
                            "score": item.get("score", 0.0),
                        }
                    )
                return results
            except Exception as exc:
                raise _map_exception(exc) from exc

    async def v4_improve(self, **kwargs: Any) -> dict[str, Any]:
        # Improve is essentially creating a new revision
        dataset_id = kwargs.get("dataset_id") or self._settings.default_dataset_id
        document_id = kwargs["document_id"]
        content = kwargs["content"]

        async with self._client() as client:
            try:
                resp = await client.create_revision(
                    tenant_id=self._settings.default_tenant_id,
                    workspace_id=self._settings.default_workspace_id,
                    dataset_id=dataset_id,
                    document_id=document_id,
                    revision_id=f"rev_{uuid.uuid4().hex[:8]}",
                    revision_number=kwargs.get("revision_number", 2),
                    content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                )
                return resp
            except Exception as exc:
                raise _map_exception(exc) from exc

    async def v4_forget(self, **kwargs: Any) -> dict[str, Any]:
        dataset_id = kwargs.get("dataset_id") or self._settings.default_dataset_id
        document_id = kwargs["document_id"]

        async with self._client() as client:
            try:
                resp = await client.purge_document(
                    tenant_id=self._settings.default_tenant_id,
                    workspace_id=self._settings.default_workspace_id,
                    dataset_id=dataset_id,
                    document_id=document_id,
                )
                return resp
            except Exception as exc:
                raise _map_exception(exc) from exc

    def _client(self) -> AsyncMesaV4Client:
        return AsyncMesaV4Client(
            base_url=self._settings.base_url,
            api_key=self._settings.api_key,
            timeout=10.0,
            max_retries=2,
        )


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
