"""First-class ContextBuilder combining current session logs, long-term canonical memories, temporal truth, provenance, and token budget management."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from mesa_memory.adapter.tokenizer import count_tokens
from mesa_memory.security import untrusted_memory
from mesa_storage.dao import MemoryDAO

TRUST_HEADER = untrusted_memory.TRUST_HEADER
TAG_OPEN = untrusted_memory.TAG_OPEN
TAG_CLOSE = untrusted_memory.TAG_CLOSE
render_untrusted_memory = untrusted_memory.render_untrusted_memory
MAX_EVIDENCE_SPAN_CHARS = 2000

_GRAPH_PATH_SEQUENCE_FIELDS = (
    "assertion_ids",
    "entity_ids",
    "edge_directions",
    "predicates",
)


def _fact_assertion_id(fact: dict[str, Any]) -> str:
    return str(fact.get("assertion_id") or fact.get("_assertion_id") or "")


def _visible_graph_path(path: dict[str, Any]) -> dict[str, Any]:
    """Keep only deterministic structural graph-proof fields."""
    visible: dict[str, Any] = {}
    path_id = path.get("graph_path_id")
    if path_id:
        visible["graph_path_id"] = str(path_id)
    for key in _GRAPH_PATH_SEQUENCE_FIELDS:
        value = path.get(key)
        if isinstance(value, (list, tuple)):
            visible[key] = [str(item) for item in value]
    seed_id = path.get("seed_id")
    if seed_id:
        visible["seed_id"] = str(seed_id)
    return visible


def _valid_graph_paths(
    memory: dict[str, Any], principal_assertion_id: str
) -> list[dict[str, Any]]:
    """Return complete paths that can be mapped to rendered provenance facts."""
    facts_by_assertion_id = {
        _fact_assertion_id(fact): fact
        for fact in memory.get("facts", [])
        if _fact_assertion_id(fact)
    }
    if not principal_assertion_id or not facts_by_assertion_id:
        return []

    paths: list[tuple[int, dict[str, Any]]] = []
    for original_index, raw_path in enumerate(memory.get("_graph_paths", [])):
        if not isinstance(raw_path, dict):
            continue
        path = _visible_graph_path(raw_path)
        assertion_ids = path.get("assertion_ids", [])
        entity_ids = path.get("entity_ids", [])
        edge_directions = path.get("edge_directions", [])
        predicates = path.get("predicates", [])
        if (
            not path.get("graph_path_id")
            or not assertion_ids
            or assertion_ids[-1] != principal_assertion_id
            or any(
                assertion_id not in facts_by_assertion_id
                for assertion_id in assertion_ids
            )
            or len(entity_ids) != len(assertion_ids) + 1
            or len(edge_directions) != len(assertion_ids)
            or len(predicates) != len(assertion_ids)
        ):
            continue
        paths.append((original_index, path))

    # Exact structural duplicates add no proof content.  Keep the representative
    # with the stable smallest path identity before selecting a minimum proof.
    deduplicated: dict[
        tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]],
        tuple[int, dict[str, Any]],
    ] = {}
    for original_index, path in paths:
        signature = (
            tuple(str(item) for item in path.get("assertion_ids", [])),
            tuple(str(item) for item in path.get("entity_ids", [])),
            tuple(str(item) for item in path.get("edge_directions", [])),
            tuple(str(item) for item in path.get("predicates", [])),
        )
        existing = deduplicated.get(signature)
        if existing is None or str(path["graph_path_id"]) < str(
            existing[1]["graph_path_id"]
        ):
            deduplicated[signature] = (original_index, path)

    return [
        path
        for _, path in sorted(
            deduplicated.values(),
            key=lambda entry: (
                len(entry[1]["assertion_ids"]),
                str(entry[1]["graph_path_id"]),
                entry[0],
            ),
        )
    ]


def _minimum_complete_graph_proof(
    memory: dict[str, Any], principal_assertion_id: str
) -> dict[str, Any] | None:
    """Select one shortest complete path using only stable graph structure."""
    valid_paths = _valid_graph_paths(memory, principal_assertion_id)
    if not valid_paths:
        return None
    selected_path = valid_paths[0]
    facts_by_assertion_id = {
        _fact_assertion_id(fact): fact
        for fact in memory["facts"]
        if _fact_assertion_id(fact)
    }
    compact = deepcopy(memory)
    compact["facts"] = [
        deepcopy(facts_by_assertion_id[assertion_id])
        for assertion_id in selected_path["assertion_ids"]
    ]
    compact["_graph_paths"] = [deepcopy(selected_path)]
    compact["_selected_graph_path"] = deepcopy(selected_path)
    compact["_proof_compacted"] = (
        len(valid_paths) != 1
        or [
            _fact_assertion_id(fact)
            for fact in memory["facts"]
            if _fact_assertion_id(fact)
        ]
        != selected_path["assertion_ids"]
    )
    return compact


def _selected_retrieval_provenance(
    retrieval_provenance: dict[str, Any],
    selected_path: dict[str, Any],
) -> dict[str, Any]:
    """Describe the graph proof that actually survived context packing."""
    selected = deepcopy(retrieval_provenance)
    original_paths = retrieval_provenance.get("graph_paths", [])
    assertion_ids = list(selected_path["assertion_ids"])
    entity_ids = list(selected_path["entity_ids"])
    edge_directions = list(selected_path["edge_directions"])
    predicates = list(selected_path["predicates"])
    selected.update(
        {
            "graph_hop_count": len(assertion_ids),
            "graph_seed_entity_id": entity_ids[0],
            "graph_seed_entity_ids": [entity_ids[0]],
            "graph_distinct_seed_count": 1,
            "graph_target_entity_id": entity_ids[-1],
            "graph_target_entity_ids": [entity_ids[-1]],
            "graph_path_assertion_ids": assertion_ids,
            "graph_path_entity_ids": entity_ids,
            "graph_path_id": selected_path["graph_path_id"],
            "graph_edge_directions": edge_directions,
            "graph_predicates": predicates,
            "graph_direction": (
                edge_directions[0] if len(set(edge_directions)) == 1 else "mixed"
            ),
            "graph_support_count": 1,
            "graph_supporting_assertion_ids": assertion_ids,
            "graph_paths": [deepcopy(selected_path)],
            "graph_compaction": {
                "applied": True,
                "policy": "shortest_path_then_graph_path_id",
                "original_path_count": (
                    len(original_paths) if isinstance(original_paths, list) else 0
                ),
                "selected_graph_path_id": selected_path["graph_path_id"],
            },
        }
    )
    return selected


def _count_tokens(text: str) -> int:
    """Canonical tokenizer counting path for ContextBuilder."""
    if not text:
        return 0
    return int(count_tokens(text, adapter_type="openai", strict=True))


def _render_context(
    session_records: list[dict[str, Any]],
    memory_records: list[dict[str, Any]],
) -> str:
    """Render structured untrusted evidence with explicit boundary tags."""
    return str(
        render_untrusted_memory(
            [
                ("Current Session Information", session_records),
                ("Long-Term Canonical Truth", memory_records),
            ]
        )
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
        jurisdiction: str | None = None,
        valid_at: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        include_provenance: bool = True,
        max_evidence_span_chars: int | None = None,
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
                    jurisdiction=jurisdiction,
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
        seen_evidence_ids: set[str] = set()
        for idx, item in enumerate(canonical_memories):
            evidence_identity = str(
                item.get("assertion_id")
                or item.get("evidence_id")
                or item.get("candidate_id")
                or ""
            )
            if evidence_identity and evidence_identity in seen_evidence_ids:
                continue
            if evidence_identity:
                seen_evidence_ids.add(evidence_identity)
            entity = (
                item.get("entity", {}) if isinstance(item.get("entity"), dict) else {}
            )
            name = str(entity.get("canonical_name", ""))
            provenance = item.get("provenance", [])
            facts: list[dict[str, Any]] = []
            if provenance and isinstance(provenance, list):
                for provenance_index, p in enumerate(provenance):
                    predicate = str(p.get("predicate", "") or "")
                    val = p.get("literal_value")
                    if val is None:
                        obj_name = p.get("object_name")
                        obj_id = p.get("object_entity_id")
                        if obj_name:
                            val = obj_name
                        elif obj_id:
                            # Avoid leaking opaque internal UUIDs into model-visible context
                            if (
                                str(obj_id).startswith(("e_", "ent_", "ast_"))
                                or len(str(obj_id)) > 24
                            ):
                                val = p.get("predicate", "related_entity")
                            else:
                                val = str(obj_id)
                        else:
                            val = ""
                    val_str = str(val)
                    direction = str(p.get("direction") or "asserted")
                    fact_dict: dict[str, Any] = {
                        "predicate": predicate,
                        "value": val_str,
                        "direction": direction,
                        "_source_provenance_index": provenance_index,
                    }
                    assertion_id = p.get("assertion_id")
                    if assertion_id:
                        fact_dict["_assertion_id"] = str(assertion_id)
                    subject_name = p.get("subject_name") or name
                    if subject_name:
                        fact_dict["subject"] = str(subject_name)
                    object_type = p.get("object_type")
                    if object_type:
                        fact_dict["object_type"] = str(object_type)
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
                            # Evidence is retained intact here.  The token
                            # budget below removes whole evidence records
                            # rather than silently cutting a legal condition.
                            fact_dict["evidence_span"] = str(evidence_span)
                        jurisdiction = p.get("jurisdiction")
                        if jurisdiction:
                            fact_dict["jurisdiction"] = str(jurisdiction)
                        authority = p.get("authority_level")
                        if authority:
                            fact_dict["authority_level"] = str(authority)
                    facts.append(fact_dict)

            retrieval_prov = item.get("retrieval_provenance") or {}
            graph_hop_count = int(retrieval_prov.get("graph_hop_count") or 0)
            is_atomic_proof = (
                graph_hop_count > 1
                or len(retrieval_prov.get("graph_path_assertion_ids", [])) > 1
                or bool(retrieval_prov.get("is_atomic_proof"))
            )
            graph_paths = [
                _visible_graph_path(path)
                for path in retrieval_prov.get("graph_paths", [])
                if isinstance(path, dict)
            ]
            if graph_paths:
                for fact in facts:
                    if fact.get("_assertion_id"):
                        fact["assertion_id"] = fact["_assertion_id"]

            memory_record: dict[str, Any] = {
                "type": "canonical_memory",
                "entity": name,
                "facts": facts,
                "_raw_index": idx,
                "_is_atomic_proof": is_atomic_proof,
                "_principal_assertion_id": str(
                    item.get("assertion_id")
                    or item.get("evidence_id")
                    or retrieval_prov.get("matched_assertion_id")
                    or retrieval_prov.get("assertion_id")
                    or ""
                ),
                "_retrieval_provenance": deepcopy(retrieval_prov),
            }
            if graph_paths:
                # Structural path metadata drives atomic proof selection and
                # remains available in canonical retrieval provenance.  It is
                # not evidence text, so keep it out of the model token budget.
                memory_record["_graph_paths"] = graph_paths
            memory_records.append(memory_record)

        # 4. Enforce the hard token budget in fused retrieval order.  Full
        # evidence wins when it fits.  Otherwise graph evidence may fall back
        # only to one structurally complete path; non-graph evidence may shed
        # whole trailing facts.  No text is semantically summarized.
        cur_memories: list[dict[str, Any]] = []
        cur_sessions = list(session_records)

        def _model_visible_records(
            records: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            return [
                {
                    key: (
                        [
                            {
                                fact_key: fact_value
                                for fact_key, fact_value in fact.items()
                                if not fact_key.startswith("_")
                            }
                            for fact in value
                        ]
                        if key == "facts"
                        else value
                    )
                    for key, value in record.items()
                    if not key.startswith("_")
                }
                for record in records
            ]

        budget_rejections: list[dict[str, Any]] = []

        def _fits(records: list[dict[str, Any]]) -> bool:
            return (
                _count_tokens(_render_context([], _model_visible_records(records)))
                <= token_budget
            )

        # Prepare one indivisible minimum representation per ranked candidate.
        # Selection below keeps fused rank as the anchor, while reserving one
        # complete slot for compact evidence when any ranked pair can fit.  This
        # prevents an early verbose minimum from greedily consuming the budget
        # before later candidates are considered.
        prepared_memories: list[tuple[dict[str, Any], dict[str, Any], Any]] = []
        for memory in memory_records:
            if not memory["facts"]:
                continue
            full_memory = deepcopy(memory)
            candidate_id = canonical_memories[memory["_raw_index"]].get("candidate_id")
            if memory.get("_is_atomic_proof"):
                compact = _minimum_complete_graph_proof(
                    memory, memory["_principal_assertion_id"]
                )
                if compact is not None:
                    selected_path = compact["_selected_graph_path"]
                    compact["_retrieval_provenance"] = _selected_retrieval_provenance(
                        memory["_retrieval_provenance"], selected_path
                    )
                    minimum_memory = compact
                else:
                    # Older atomic metadata without a complete graph_paths
                    # contract cannot be split safely.
                    minimum_memory = full_memory
            else:
                minimum_memory = deepcopy(memory)
                minimum_memory["facts"] = minimum_memory["facts"][:1]
            prepared_memories.append((full_memory, minimum_memory, candidate_id))

        viable_memories: list[tuple[dict[str, Any], dict[str, Any], Any]] = []
        for full_memory, minimum_memory, candidate_id in prepared_memories:
            if not _fits([minimum_memory]):
                budget_rejections.append(
                    {
                        "candidate_id": candidate_id,
                        "reason": (
                            "PROOF_EXCEEDS_CONTEXT_BUDGET"
                            if full_memory.get("_is_atomic_proof")
                            else "EVIDENCE_EXCEEDS_CONTEXT_BUDGET"
                        ),
                    }
                )
            else:
                viable_memories.append((full_memory, minimum_memory, candidate_id))

        def _ordered_minimums(positions: set[int]) -> list[dict[str, Any]]:
            return [
                minimum_memory
                for position, (_, minimum_memory, _) in enumerate(viable_memories)
                if position in positions
            ]

        selected_positions: set[int] = set()
        selected_pair: tuple[int, int] | None = None
        for anchor in range(len(viable_memories)):
            fitting_partners = [
                partner
                for partner in range(len(viable_memories))
                if partner != anchor and _fits(_ordered_minimums({anchor, partner}))
            ]
            if fitting_partners:
                # Fused rank remains the priority signal: reserve the earliest
                # ranked partner that fits with this anchor.  A later, smaller
                # candidate is considered only when earlier partners cannot fit.
                partner = min(fitting_partners)
                selected_pair = (anchor, partner)
                break

        if selected_pair is not None:
            selected_positions.update(selected_pair)
        elif viable_memories:
            selected_positions.add(0)

        # Once the rank anchor and compact companion are reserved, consume all
        # remaining capacity in the original fused order.
        for position in range(len(viable_memories)):
            if position in selected_positions:
                continue
            proposed = {*selected_positions, position}
            if _fits(_ordered_minimums(proposed)):
                selected_positions = proposed

        cur_memories = _ordered_minimums(selected_positions)
        for position, (_, _, candidate_id) in enumerate(viable_memories):
            if position not in selected_positions:
                budget_rejections.append(
                    {
                        "candidate_id": candidate_id,
                        "reason": "CONTEXT_BUDGET_EXHAUSTED",
                    }
                )

        # Enrich retained candidates in the same fused order. Atomic graph
        # candidates upgrade only as a whole; non-graph candidates add whole
        # facts one at a time.
        retained_by_index = {
            memory["_raw_index"]: position
            for position, memory in enumerate(cur_memories)
        }
        for full_memory, minimum_memory, _ in prepared_memories:
            retained_position = retained_by_index.get(full_memory["_raw_index"])
            if retained_position is None:
                continue
            if full_memory.get("_is_atomic_proof"):
                if minimum_memory.get("_proof_compacted"):
                    enriched = list(cur_memories)
                    enriched[retained_position] = full_memory
                    if _fits(enriched):
                        cur_memories = enriched
                continue

            for fact in full_memory["facts"][1:]:
                enriched_memory = deepcopy(cur_memories[retained_position])
                enriched_memory["facts"].append(deepcopy(fact))
                enriched = list(cur_memories)
                enriched[retained_position] = enriched_memory
                if not _fits(enriched):
                    break
                cur_memories = enriched

        compacted_graph_proof_count = sum(
            1 for memory in cur_memories if memory.get("_proof_compacted")
        )

        formatted_context = _render_context(
            cur_sessions, _model_visible_records(cur_memories)
        )
        actual_tokens = _count_tokens(formatted_context)

        # Current-session chatter yields budget first
        while actual_tokens > token_budget and cur_sessions:
            cur_sessions.pop()
            formatted_context = _render_context(
                cur_sessions, _model_visible_records(cur_memories)
            )
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
                retained_facts = matching_cur.get("facts", [])
                visible_item: dict[str, Any] = {
                    "entity": {
                        "canonical_name": str(
                            raw_mem.get("entity", {}).get("canonical_name", "")
                        )
                    },
                    "provenance": [
                        {
                            key: value
                            for key, value in fact.items()
                            if not key.startswith("_")
                        }
                        for fact in retained_facts
                    ],
                }
                for key in (
                    "candidate_id",
                    "evidence_id",
                    "assertion_id",
                    "source_chunk_id",
                    "document_id",
                    "scope_identity",
                    "rrf_score",
                    "final_score",
                ):
                    if key in raw_mem:
                        visible_item[key] = raw_mem[key]
                if "retrieval_provenance" in raw_mem:
                    visible_item["retrieval_provenance"] = matching_cur.get(
                        "_retrieval_provenance", raw_mem["retrieval_provenance"]
                    )
                model_visible_memories.append(visible_item)

        renderable_candidate_count = sum(
            1 for memory in memory_records if memory["facts"]
        )
        if model_visible_memories:
            context_status = "CONTEXT_BUILT_SUCCESSFULLY"
        elif canonical_memories and renderable_candidate_count == 0:
            context_status = "ALL_EVIDENCE_INVALID"
        elif canonical_memories:
            context_status = "VALID_EVIDENCE_EXCEEDS_CONTEXT_BUDGET"
        elif cur_sessions:
            context_status = "CONTEXT_BUILT_SUCCESSFULLY"
        else:
            context_status = "NO_RETRIEVAL_EVIDENCE"

        return {
            "formatted_context": formatted_context,
            "session_logs": cur_sessions,
            "canonical_memories": model_visible_memories,
            "model_visible_memories": model_visible_memories,
            "_debug_raw_retrieval": canonical_memories,
            "token_budget": token_budget,
            "estimated_token_count": actual_tokens,
            "actual_token_count": actual_tokens,
            "context_status": context_status,
            "context_diagnostics": {
                "retrieval_candidate_count": len(canonical_memories),
                "renderable_candidate_count": renderable_candidate_count,
                "retained_memory_count": len(model_visible_memories),
                "compacted_graph_proof_count": compacted_graph_proof_count,
                "budget_rejection_count": len(budget_rejections),
                "budget_rejections": budget_rejections,
            },
        }
