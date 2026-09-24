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
MAX_EVIDENCE_SPAN_CHARS = 2000


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
        max_evidence_span_chars: int = MAX_EVIDENCE_SPAN_CHARS,
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
        for idx, item in enumerate(canonical_memories):
            entity = item.get("entity", {}) if isinstance(item.get("entity"), dict) else {}
            name = str(entity.get("canonical_name", ""))
            provenance = item.get("provenance", [])
            facts: list[dict[str, Any]] = []
            if provenance and isinstance(provenance, list):
                for p in provenance:
                    predicate = str(p.get("predicate", "") or "")
                    val = p.get("literal_value")
                    if val is None:
                        obj_name = p.get("object_name")
                        obj_id = p.get("object_entity_id")
                        if obj_name:
                            val = obj_name
                        elif obj_id:
                            # Avoid leaking opaque internal UUIDs into model-visible context
                            if str(obj_id).startswith(("e_", "ent_", "ast_")) or len(str(obj_id)) > 24:
                                val = p.get("predicate", "related_entity")
                            else:
                                val = str(obj_id)
                        else:
                            val = ""
                    val_str = str(val)
                    direction = str(p.get("direction") or "forward")
                    fact_dict: dict[str, Any] = {
                        "predicate": predicate,
                        "value": val_str,
                        "direction": direction,
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
                                :max_evidence_span_chars
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
                    "_raw_index": idx,
                }
            )

        # 4. Enforce hard token budget via actual tokenizer counting and granular fact trimming
        cur_memories = [
            {
                "type": m["type"],
                "entity": m["entity"],
                "facts": list(m["facts"]),
                "_raw_index": m["_raw_index"],
            }
            for m in memory_records
        ]
        cur_sessions = list(session_records)

        formatted_context = _render_context(cur_sessions, cur_memories)
        actual_tokens = _count_tokens(formatted_context)

        # Current-session chatter yields budget first
        while actual_tokens > token_budget and cur_sessions:
            cur_sessions.pop()
            formatted_context = _render_context(cur_sessions, cur_memories)
            actual_tokens = _count_tokens(formatted_context)

        # Fine-grained fact/evidence-level trimming:
        # Prevent any single large entity from crowding out others.
        while actual_tokens > token_budget and cur_memories:
            # Check if any memory has > 1 fact; if so, prune the lowest-priority fact from the end
            pruned_fact = False
            for mem in reversed(cur_memories):
                if len(mem["facts"]) > 1:
                    mem["facts"].pop()
                    pruned_fact = True
                    break

            # If all remaining memories have at most 1 fact, prune the lowest-ranked memory entity
            if not pruned_fact:
                cur_memories.pop()

            formatted_context = _render_context(cur_sessions, cur_memories)
            actual_tokens = _count_tokens(formatted_context)

        # If empty structural wrapper itself exceeds token_budget (tiny budget case)
        if actual_tokens > token_budget:
            formatted_context = ""
            actual_tokens = 0
            cur_memories = []
            cur_sessions = []

        # Construct authoritative model_visible_memories strictly matching formatted_context
        retained_indices = {m["_raw_index"]: m for m in cur_memories}
        model_visible_memories: list[dict[str, Any]] = []
        for idx, raw_mem in enumerate(canonical_memories):
            if idx in retained_indices:
                matching_cur = retained_indices[idx]
                visible_item = dict(raw_mem)
                if matching_cur.get("facts"):
                    retained_preds = {f["predicate"] for f in matching_cur["facts"]}
                    if "provenance" in visible_item and isinstance(visible_item["provenance"], list):
                        visible_item["provenance"] = [
                            p
                            for p in raw_mem.get("provenance", [])
                            if (p.get("predicate", "") or "") in retained_preds
                        ]
                model_visible_memories.append(visible_item)

        return {
            "formatted_context": formatted_context,
            "session_logs": cur_sessions,
            "canonical_memories": model_visible_memories,
            "model_visible_memories": model_visible_memories,
            "_debug_raw_retrieval": canonical_memories,
            "token_budget": token_budget,
            "estimated_token_count": actual_tokens,
            "actual_token_count": actual_tokens,
        }
