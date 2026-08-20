"""First-class ContextBuilder combining current session logs, long-term canonical memories, temporal truth, provenance, and token budget management."""

from __future__ import annotations

from typing import Any

from mesa_memory.adapter.tokenizer import count_tokens
from mesa_memory.security import untrusted_memory
from mesa_storage.dao import MemoryDAO

TRUST_HEADER = untrusted_memory.TRUST_HEADER
TAG_OPEN = untrusted_memory.TAG_OPEN
TAG_CLOSE = untrusted_memory.TAG_CLOSE
render_untrusted_memory = untrusted_memory.render_untrusted_memory
MAX_EVIDENCE_SPAN_CHARS = 200


def _count_tokens(text: str) -> int:
    """Canonical tokenizer counting path for ContextBuilder."""
    if not text:
        return 0
    return count_tokens(text, adapter_type="openai", strict=True)


def _render_context(
    session_records: list[dict[str, Any]],
    memory_records: list[dict[str, Any]],
) -> str:
    """Render structured untrusted evidence with explicit boundary tags."""
    return render_untrusted_memory(
        [
            ("Current Session Information", session_records),
            ("Long-Term Canonical Truth", memory_records),
        ]
    )


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
        valid_from: str | None = None,
        valid_to: str | None = None,
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
                    valid_from=valid_from,
                    valid_to=valid_to,
                )

        # 3. Construct candidate structured evidence records
        session_records: list[dict[str, Any]] = []
        for log in session_logs:
            session_records.append(
                {
                    "type": "session_log",
                    "content": str(log.get("content", "")),
                }
            )

        memory_records: list[dict[str, Any]] = []
        for item in canonical_memories:
            entity = item.get("entity", {})
            name = str(entity.get("canonical_name", ""))
            provenance = item.get("provenance", [])
            facts: list[dict[str, Any]] = []
            if provenance:
                for p in provenance:
                    predicate = str(p.get("predicate", "") or "")
                    val = p.get("literal_value")
                    if val is None:
                        val = p.get("object_name") or p.get("object_entity_id") or ""
                    val_str = str(val)
                    fact_dict: dict[str, Any] = {
                        "predicate": predicate,
                        "value": val_str,
                    }
                    if include_provenance:
                        source_ref = p.get("source_ref")
                        if source_ref:
                            fact_dict["source_ref"] = str(source_ref)
                        doc_id = p.get("document_id")
                        if doc_id:
                            fact_dict["document_id"] = str(doc_id)
                        rev_id = p.get("revision_id")
                        if rev_id:
                            fact_dict["revision_id"] = str(rev_id)
                        chunk_id = p.get("chunk_id")
                        if chunk_id:
                            fact_dict["chunk_id"] = str(chunk_id)
                        evidence_span = p.get("evidence_span")
                        if evidence_span:
                            fact_dict["evidence_span"] = str(evidence_span)[
                                :MAX_EVIDENCE_SPAN_CHARS
                            ]
                        jurisdiction = p.get("jurisdiction")
                        if jurisdiction:
                            fact_dict["jurisdiction"] = str(jurisdiction)
                        authority = p.get("authority_level")
                        if authority:
                            fact_dict["authority_level"] = str(authority)
                    facts.append(fact_dict)

            memory_records.append(
                {
                    "type": "canonical_memory",
                    "entity": name,
                    "facts": facts,
                }
            )

        # 4. Enforce hard token budget via actual tokenizer counting and ranking-aware trimming
        cur_memories = list(memory_records)
        cur_sessions = list(session_records)

        formatted_context = _render_context(cur_sessions, cur_memories)
        actual_tokens = _count_tokens(formatted_context)

        # Current-session chatter yields budget first so it cannot evict all
        # ranked long-term evidence. Records are always removed from the end,
        # preserving retrieval order within each source.
        while actual_tokens > token_budget and cur_sessions:
            cur_sessions.pop()
            formatted_context = _render_context(cur_sessions, cur_memories)
            actual_tokens = _count_tokens(formatted_context)

        while actual_tokens > token_budget and cur_memories:
            cur_memories.pop()
            formatted_context = _render_context(cur_sessions, cur_memories)
            actual_tokens = _count_tokens(formatted_context)

        # If empty structural wrapper itself exceeds token_budget (tiny budget case)
        if actual_tokens > token_budget:
            formatted_context = ""
            actual_tokens = 0

        return {
            "formatted_context": formatted_context,
            "session_logs": session_logs,
            "canonical_memories": canonical_memories,
            "token_budget": token_budget,
            "estimated_token_count": actual_tokens,
            "actual_token_count": actual_tokens,
        }
