"""Validation, scoping and output shaping between MCP tools and MESA."""

from __future__ import annotations

from typing import Any

from mesa_memory.security.input_validation import validate_write_payload
from mesa_memory.security.untrusted_memory import render_untrusted_memory

from . import __version__
from .configuration import MCPSettings
from .errors import MCPError
from .security import MEMORY_TYPES
from .service import MemoryServiceProtocol, V4MemoryServiceProtocol

_MAX_CONTENT_LENGTH = 20_000
_MAX_QUERY_LENGTH = 2_000


class MesaMCPAdapter:
    def __init__(
        self,
        service: MemoryServiceProtocol,
        settings: MCPSettings,
        v4_service: V4MemoryServiceProtocol | None = None,
    ):
        self._service = service
        self._settings = settings
        self._v4_service = v4_service

    async def health(self) -> dict[str, Any]:
        health = await self._service.health()
        response = {
            "status": health.get("status", "healthy"),
            "server_version": __version__,
            "mesa": health,
            "transport": "stdio",
        }
        if self._v4_service is not None:
            response["v4_capability"] = await self._v4_service.v4_capability()
        return response

    async def get_context(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = _required_string(arguments, "query", max_length=_MAX_QUERY_LENGTH)
        retrieval_mode = arguments.get("retrieval_mode", "single")
        if not isinstance(retrieval_mode, str) or retrieval_mode not in {
            "single",
            "adaptive",
        }:
            raise MCPError(
                "INVALID_ARGUMENT",
                "retrieval_mode must be 'single' or 'adaptive'",
            )
        token_budget = arguments.get(
            "token_budget", self._settings.context_default_token_budget
        )
        if (
            isinstance(token_budget, bool)
            or not isinstance(token_budget, int)
            or not 1 <= token_budget <= self._settings.context_max_token_budget
        ):
            raise MCPError(
                "INVALID_ARGUMENT",
                f"token_budget must be between 1 and {self._settings.context_max_token_budget}",
            )
        include_types = arguments.get("include_types")
        if include_types is not None and (
            not isinstance(include_types, list)
            or any(item not in MEMORY_TYPES for item in include_types)
        ):
            raise MCPError(
                "INVALID_ARGUMENT", "include_types contains an unsupported value"
            )
        if not self._v4_service:
            raise MCPError("UNIMPLEMENTED", "context requires the V4 service")

        context_arguments = {
            "dataset_id": arguments.get("dataset_id"),
            "query": query,
            "token_budget": token_budget,
            "jurisdiction": arguments.get("jurisdiction"),
            "valid_at": arguments.get("valid_at"),
            "valid_from": arguments.get("valid_from"),
            "valid_to": arguments.get("valid_to"),
        }
        if retrieval_mode == "adaptive":
            context_arguments["retrieval_mode"] = retrieval_mode
        raw = await self._v4_service.v4_context(**context_arguments)
        return {
            key: raw[key]
            for key in (
                "tenant_id",
                "workspace_id",
                "dataset_ids",
                "agent_id",
                "session_id",
                "context",
                "context_status",
                "estimated_token_count",
            )
            if key in raw
        }

    async def mesa_remember(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._v4_service:
            raise MCPError("UNIMPLEMENTED", "V4 service is not enabled")
        dataset_id = arguments.get("dataset_id")
        content = _required_string(arguments, "content", max_length=_MAX_CONTENT_LENGTH)
        metadata = arguments.get("metadata", {})
        if not isinstance(metadata, dict):
            raise MCPError("INVALID_ARGUMENT", "metadata must be an object")
        try:
            validate_write_payload(content, metadata)
        except ValueError as exc:
            raise MCPError("INVALID_ARGUMENT", str(exc)) from exc
        return await self._v4_service.v4_remember(
            dataset_id=dataset_id,
            content=content,
            metadata=metadata,
            **{
                key: arguments[key]
                for key in (
                    "title",
                    "source_ref",
                    "evidence_span",
                    "document_id",
                    "idempotency_key",
                )
                if arguments.get(key) is not None
            },
        )

    async def mesa_recall(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._v4_service:
            raise MCPError("UNIMPLEMENTED", "V4 service is not enabled")
        dataset_id = arguments.get("dataset_id")
        query = _required_string(arguments, "query", max_length=_MAX_QUERY_LENGTH)
        raw = await self._v4_service.v4_recall(
            dataset_id=dataset_id,
            query=query,
            limit=arguments.get("limit"),
            jurisdiction=arguments.get("jurisdiction"),
            valid_at=arguments.get("valid_at"),
            valid_from=arguments.get("valid_from"),
            valid_to=arguments.get("valid_to"),
        )
        if isinstance(raw, dict):
            raw_results = raw.get("results", [])
            degraded_sources = list(raw.get("degraded_sources", []))
        else:
            raw_results = raw
            degraded_sources = list(getattr(raw, "degraded_sources", []))

        wrapped_results = []
        for item in raw_results:
            chunk_text = str(item.get("content") or "")
            # Stored text is always wrapped, even when it already looks like one.
            wrapped = render_untrusted_memory(
                [("Retrieved Memory", [{"content": chunk_text}])]
            )
            wrapped_results.append(
                {
                    "memory_id": item.get("memory_id") or item.get("chunk_id"),
                    "document_id": item.get("document_id"),
                    "chunk_id": item.get("chunk_id"),
                    "score": item.get("score", 0.0),
                    "content": wrapped,
                }
            )
        return {
            "results": wrapped_results,
            "total": len(wrapped_results),
            "degraded_sources": degraded_sources,
        }

    async def mesa_improve(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._v4_service:
            raise MCPError("UNIMPLEMENTED", "V4 service is not enabled")
        dataset_id = arguments.get("dataset_id")
        document_id = _required_string(arguments, "document_id", max_length=256)
        content = _required_string(arguments, "content", max_length=_MAX_CONTENT_LENGTH)
        metadata = arguments.get("metadata", {})
        if not isinstance(metadata, dict):
            raise MCPError("INVALID_ARGUMENT", "metadata must be an object")
        try:
            validate_write_payload(content, metadata)
        except ValueError as exc:
            raise MCPError("INVALID_ARGUMENT", str(exc)) from exc
        return await self._v4_service.v4_improve(
            dataset_id=dataset_id,
            document_id=document_id,
            content=content,
            metadata=metadata,
            **{
                key: arguments[key]
                for key in (
                    "revision_id",
                    "chunk_id",
                    "supersedes_revision_id",
                    "idempotency_key",
                    "title",
                    "source_ref",
                    "evidence_span",
                    "revision_number",
                )
                if arguments.get(key) is not None
            },
        )

    async def mesa_forget(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._v4_service:
            raise MCPError("UNIMPLEMENTED", "V4 service is not enabled")
        dataset_id = arguments.get("dataset_id")
        document_id = _required_string(arguments, "document_id", max_length=256)
        return await self._v4_service.v4_forget(
            dataset_id=dataset_id, document_id=document_id
        )


def _required_string(arguments: dict[str, Any], field: str, *, max_length: int) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not (value := value.strip()):
        raise MCPError("INVALID_ARGUMENT", f"{field} is required")
    if len(value) > max_length:
        raise MCPError("INVALID_ARGUMENT", f"{field} exceeds {max_length} characters")
    return value
