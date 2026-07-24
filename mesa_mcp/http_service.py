"""MCP-facing adapter for the existing MESA HTTP application service."""

from __future__ import annotations

import hashlib
from typing import Any

from mesa_api.schemas import MemoryInsertRequest, MemorySearchRequest
from mesa_client.client import AsyncMesaClient, MesaAPIError, MesaNetworkError

from .configuration import MCPSettings
from .errors import MCPError


class MesaHttpMemoryService:
    """Use MESA's public API; this class never imports a storage backend."""

    def __init__(self, settings: MCPSettings):
        self._settings = settings

    async def health(self) -> dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client._request("GET", "/v3/health")
        except Exception as exc:  # mapped below to keep protocol errors stable
            raise _map_exception(exc) from exc
        return response

    async def create_memory(self, **kwargs: Any) -> dict[str, Any]:
        project_id = str(kwargs["project_id"])
        metadata = dict(kwargs["metadata"])
        metadata.update(
            {
                "mesa_mcp_namespace": kwargs["namespace"],
                "mesa_mcp_project_id": project_id,
                "memory_type": kwargs["memory_type"],
                "importance": kwargs["importance"],
                "content_sha256": hashlib.sha256(kwargs["content"].encode()).hexdigest(),
            }
        )
        if kwargs["source_file"]:
            metadata["source_file"] = kwargs["source_file"]
        if kwargs["idempotency_key"]:
            metadata["idempotency_key"] = kwargs["idempotency_key"]
        request = MemoryInsertRequest(
            agent_id=self._settings.actor_id,
            session_id=self._settings.session_id_for(project_id),
            content=kwargs["content"],
            metadata=metadata,
        )
        try:
            async with self._client() as client:
                accepted = await client.insert(request)
        except Exception as exc:
            raise _map_exception(exc) from exc
        return {
            "id": f"raw_{accepted.log_id}",
            "content": request.content,
            "memory_type": kwargs["memory_type"],
            "project_id": project_id,
            "status": accepted.status,
            "created_at": None,
            "operation": "deduplicated" if accepted.deduplicated else "created",
        }

    async def search_memories(self, **kwargs: Any) -> list[dict[str, Any]]:
        project_id = str(kwargs["project_id"])
        request = MemorySearchRequest(
            agent_id=self._settings.actor_id,
            session_id=self._settings.session_id_for(project_id),
            query=kwargs["query"],
            limit=kwargs["limit"],
        )
        try:
            async with self._client() as client:
                response = await client.search(request)
        except Exception as exc:
            raise _map_exception(exc) from exc
        requested_types = set(kwargs["memory_types"] or ())
        results: list[dict[str, Any]] = []
        for node in response.retrieved_nodes:
            # MESA's V3 result schema has no typed metadata yet.  We return
            # unknown rather than inventing a type or silently filtering data.
            if requested_types:
                continue
            score = max(0.0, min(1.0, float(node.score)))
            if score < kwargs["min_score"]:
                continue
            results.append(
                {
                    "memory_id": node.node_id,
                    "content": node.content_payload or node.entity_name,
                    "memory_type": "unknown",
                    "score": score,
                    "semantic_score": score,
                    "importance": None,
                    "source": {"type": node.source},
                }
            )
        return results

    async def get_memory(self, **kwargs: Any) -> dict[str, Any] | None:
        project_id = str(kwargs["project_id"])
        try:
            async with self._client() as client:
                response = await client._request(
                    "GET",
                    f"/v3/memory/records/{kwargs['memory_id']}",
                    params={
                        "agent_id": self._settings.actor_id,
                        "session_id": self._settings.session_id_for(project_id),
                    },
                )
        except MesaAPIError as exc:
            if exc.status_code == 404:
                return None
            raise _map_exception(exc) from exc
        except Exception as exc:
            raise _map_exception(exc) from exc
        return response.get("memory")

    def _client(self) -> AsyncMesaClient:
        return AsyncMesaClient(
            base_url=self._settings.base_url,
            api_key=self._settings.api_key,
            timeout=10.0,
            max_retries=2,
        )


def _map_exception(exc: Exception) -> MCPError:
    if isinstance(exc, MCPError):
        return exc
    if isinstance(exc, MesaNetworkError):
        return MCPError("BACKEND_UNAVAILABLE", "MESA service is unavailable", retryable=True)
    if isinstance(exc, MesaAPIError):
        if exc.status_code in {400, 422}:
            return MCPError("INVALID_ARGUMENT", "MESA rejected the request")
        if exc.status_code in {401, 403}:
            return MCPError("ACCESS_DENIED", "MESA denied access to the requested scope")
        if exc.status_code == 404:
            return MCPError("NOT_FOUND", "memory was not found")
        if exc.status_code in {408, 429, 503, 504}:
            return MCPError("BACKEND_UNAVAILABLE", "MESA service is temporarily unavailable", retryable=True)
    return MCPError("INTERNAL_ERROR", "MESA operation failed")
