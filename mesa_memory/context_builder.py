"""First-class ContextBuilder combining current session logs, long-term canonical memories, temporal truth, provenance, and token budget management."""

from __future__ import annotations

import math
from typing import Any

from mesa_storage.dao import MemoryDAO


class ContextBuilder:
    """Canonical ContextBuilder for long-term multi-session memory integration."""

    def __init__(self, dao: MemoryDAO) -> None:
        self.dao = dao

    async def build_context(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        dataset_ids: list[str],
        query: str = "",
        session_id: str | None = None,
        token_budget: int = 2048,
        valid_at: str | None = None,
        include_provenance: bool = True,
    ) -> dict[str, Any]:
        """Construct context combining current-session logs and long-term canonical truth."""
        if token_budget < 1:
            raise ValueError("token_budget must be positive")
        # 1. Fetch current session raw logs if session_id provided
        session_logs: list[dict[str, Any]] = []
        if session_id:
            raw_logs = await self.dao.get_recent_logs(agent_id, session_id, limit=20)
            session_logs = [dict(log) for log in raw_logs if log.get("content")]

        # 2. Perform canonical retrieval if query or dataset_ids provided
        canonical_memories: list[dict[str, Any]] = []
        if dataset_ids and (query or session_logs):
            search_query = query or " ".join(
                str(item.get("content", "")) for item in session_logs[:3]
            )
            if search_query.strip():
                canonical_memories = await self.dao.search_v4_memory(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    dataset_ids=dataset_ids,
                    query=search_query,
                    limit=20,
                    valid_at=valid_at,
                )

        # 3. Format context string with token budget bounding (~4 chars per token).
        # Small caller budgets are hard ceilings, not minimum context hints.
        sections: list[str] = []
        char_budget = token_budget * 4
        current_chars = 0

        def append_within_budget(line: str) -> bool:
            nonlocal current_chars
            separator = 1 if sections else 0
            available = char_budget - current_chars - separator
            if available <= 0:
                return False
            if len(line) > available:
                sections.append(line[:available])
                current_chars += available + separator
                return False
            sections.append(line)
            current_chars += len(line) + separator
            return True

        if session_logs:
            session_header = "=== Current Session Information ==="
            append_within_budget(session_header)
            for log in session_logs:
                line = f"- {log.get('content', '')}"
                if not append_within_budget(line):
                    break

        if canonical_memories:
            mem_header = "=== Long-Term Canonical Truth ==="
            append_within_budget(mem_header)

            for item in canonical_memories:
                entity = item.get("entity", {})
                name = entity.get("canonical_name", "")
                provenance = item.get("provenance", [])
                prov_str = ""
                if include_provenance and provenance:
                    facts = [
                        f"{p.get('predicate', '')}: {p.get('literal_value') or p.get('object_entity_id')}"
                        for p in provenance
                        if p.get("predicate")
                    ]
                    if facts:
                        prov_str = f" ({'; '.join(facts)})"
                line = f"- [Entity: {name}]{prov_str}"
                if not append_within_budget(line):
                    break

        formatted_context = "\n".join(sections)
        estimated_tokens = math.ceil(len(formatted_context) / 4.0)

        return {
            "formatted_context": formatted_context,
            "session_logs": session_logs,
            "canonical_memories": canonical_memories,
            "token_budget": token_budget,
            "estimated_token_count": estimated_tokens,
        }
