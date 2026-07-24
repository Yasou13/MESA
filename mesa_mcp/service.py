"""The narrow service contract consumed by the MCP adapter."""

from __future__ import annotations

from typing import Any, Protocol


class MemoryServiceProtocol(Protocol):
    async def health(self) -> dict[str, Any]: ...

    async def create_memory(
        self,
        *,
        content: str,
        namespace: str,
        project_id: str,
        actor_id: str,
        memory_type: str,
        importance: float,
        metadata: dict[str, Any],
        source_file: str | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]: ...

    async def search_memories(
        self,
        *,
        query: str,
        namespace: str,
        project_id: str,
        actor_id: str,
        limit: int,
        min_score: float,
        memory_types: list[str] | None,
    ) -> list[dict[str, Any]]: ...

    async def get_memory(
        self,
        *,
        memory_id: str,
        namespace: str,
        project_id: str,
        actor_id: str,
    ) -> dict[str, Any] | None: ...
