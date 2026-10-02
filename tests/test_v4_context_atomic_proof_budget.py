import copy
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mesa_memory.context_builder import ContextBuilder


@pytest.fixture(autouse=True)
def _deterministic_token_counter(monkeypatch):
    monkeypatch.setattr(
        "mesa_memory.context_builder._count_tokens",
        lambda text: len(text.encode("utf-8")),
    )


async def _build_context(
    candidates: list[dict[str, Any]], *, token_budget: int
) -> tuple[dict[str, Any], AsyncMock]:
    dao = AsyncMock()
    dao.get_recent_logs.return_value = []
    dao.search_v4_memory.return_value = candidates
    context = await ContextBuilder(dao).build_context(
        tenant_id="tenant-1",
        agent_id="agent-1",
        dataset_ids=["dataset-1"],
        query="target",
        token_budget=token_budget,
    )
    return context, dao


def _assertion(
    assertion_id: str,
    *,
    subject: str,
    predicate: str,
    value: str,
    evidence_span: str,
) -> dict[str, str]:
    return {
        "assertion_id": assertion_id,
        "subject_name": subject,
        "predicate": predicate,
        "object_name": value,
        "direction": "forward",
        "source_ref": f"source:{assertion_id}",
        "document_id": "document-1",
        "revision_id": "revision-1",
        "chunk_id": f"chunk:{assertion_id}",
        "evidence_span": evidence_span,
        "jurisdiction": "TR",
        "authority_level": "primary",
    }


def _historical_shape_candidate(index: int) -> dict[str, Any]:
    target_id = f"target-{index}"
    short_bridge_id = f"short-bridge-{index}"
    long_bridge_a_id = f"long-bridge-a-{index}"
    long_bridge_b_id = f"long-bridge-b-{index}"
    return {
        "entity": {"canonical_name": f"Target {index}"},
        "candidate_id": target_id,
        "evidence_id": target_id,
        "assertion_id": target_id,
        "source_chunk_id": f"chunk:{target_id}",
        "document_id": "document-1",
        "rrf_score": 1.0 / (61 + index),
        "scope_identity": {
            "tenant_id": "tenant-1",
            "dataset_id": "dataset-1",
            "agent_id": "agent-1",
            "jurisdiction": "TR",
            "status": "ACTIVE",
        },
        "provenance": [
            _assertion(
                target_id,
                subject=f"Bridge {index}",
                predicate="establishes",
                value=f"Target {index}",
                evidence_span="Principal target evidence.",
            ),
            _assertion(
                short_bridge_id,
                subject=f"Seed {index}",
                predicate="supports",
                value=f"Bridge {index}",
                evidence_span="Required bridge evidence.",
            ),
            _assertion(
                long_bridge_a_id,
                subject=f"Seed {index}",
                predicate="alternative_support",
                value=f"Intermediate {index}",
                evidence_span="alternative path detail " * 700,
            ),
            _assertion(
                long_bridge_b_id,
                subject=f"Intermediate {index}",
                predicate="alternative_support",
                value=f"Bridge {index}",
                evidence_span="more alternative detail " * 700,
            ),
        ],
        "retrieval_provenance": {
            "origins": ["graph"],
            "lane_ranks": {"graph": index + 1},
            "graph_hop_count": 2,
            "graph_path_assertion_ids": [short_bridge_id, target_id],
            "graph_path_id": f"path-short-{index}",
            "graph_paths": [
                {
                    "graph_path_id": f"path-long-{index}",
                    "assertion_ids": [
                        long_bridge_a_id,
                        long_bridge_b_id,
                        target_id,
                    ],
                    "entity_ids": [
                        f"seed-{index}",
                        f"intermediate-{index}",
                        f"bridge-{index}",
                        f"target-{index}",
                    ],
                    "edge_directions": ["forward", "forward", "forward"],
                    "predicates": [
                        "alternative_support",
                        "alternative_support",
                        "establishes",
                    ],
                    "seed_id": f"seed-{index}",
                    "score": 0.9,
                },
                {
                    "graph_path_id": f"path-short-{index}",
                    "assertion_ids": [short_bridge_id, target_id],
                    "entity_ids": [
                        f"seed-{index}",
                        f"bridge-{index}",
                        f"target-{index}",
                    ],
                    "edge_directions": ["forward", "forward"],
                    "predicates": ["supports", "establishes"],
                    "seed_id": f"seed-{index}",
                    "score": 0.5,
                },
            ],
        },
    }


def _graph_chain_candidates() -> list[dict[str, Any]]:
    """Mirror the one-hop plus two-hop candidates from graph integration."""
    alice_id = "d56f5d43-80f0-5afb-9461-2dac6783d36e"
    aurora_id = "ea22626f-6faf-5a88-8b68-88ae9b708ada"
    helios_id = "205a1299-54c1-5a91-9734-18fd28ab071c"
    leads_id = f"ast_m1_{alice_id}_{aurora_id}"
    uses_id = f"ast_m2_{aurora_id}_{helios_id}"
    leads = {
        "assertion_id": leads_id,
        "subject_name": "Alice",
        "predicate": "leads",
        "object_name": "Aurora",
        "direction": "forward",
    }
    uses = {
        "assertion_id": uses_id,
        "subject_name": "Aurora",
        "predicate": "uses",
        "object_name": "HeliosDB",
        "direction": "forward",
    }
    first_path = {
        "graph_path_id": "54b3b98068311f5cf572e3b1bead991e6928d70e8e614e8f77d21c3d1e436401",
        "assertion_ids": [leads_id],
        "entity_ids": [alice_id, aurora_id],
        "edge_directions": ["forward"],
        "predicates": ["leads"],
        "seed_id": alice_id,
    }
    second_path = {
        "graph_path_id": "4de89728d40c0113fb71186e56eae28e048c194ab29166c39f8ce1925a606e70",
        "assertion_ids": [leads_id, uses_id],
        "entity_ids": [alice_id, aurora_id, helios_id],
        "edge_directions": ["forward", "forward"],
        "predicates": ["leads", "uses"],
        "seed_id": alice_id,
    }
    return [
        {
            "entity": {"canonical_name": "Alice"},
            "candidate_id": leads_id,
            "evidence_id": leads_id,
            "assertion_id": leads_id,
            "provenance": [leads],
            "retrieval_provenance": {
                "origins": ["graph"],
                "lane_ranks": {"graph": 1},
                "graph_hop_count": 1,
                "graph_path_assertion_ids": [leads_id],
                "graph_paths": [first_path],
            },
        },
        {
            "entity": {"canonical_name": "HeliosDB"},
            "candidate_id": uses_id,
            "evidence_id": uses_id,
            "assertion_id": uses_id,
            "provenance": [uses, leads],
            "retrieval_provenance": {
                "origins": ["graph"],
                "lane_ranks": {"graph": 2},
                "graph_hop_count": 2,
                "graph_path_assertion_ids": [leads_id, uses_id],
                "graph_paths": [second_path],
            },
        },
    ]


@pytest.mark.asyncio
async def test_historical_shape_keeps_minimum_complete_graph_proof():
    """A compactable valid proof must not become zero evidence at 2048 tokens."""
    candidates = [_historical_shape_candidate(index) for index in range(4)]
    dao = AsyncMock()
    dao.get_recent_logs.return_value = []
    dao.search_v4_memory.return_value = candidates

    compact_candidate = copy.deepcopy(candidates[0])
    compact_candidate["provenance"] = compact_candidate["provenance"][:2]
    compact_candidate["retrieval_provenance"]["graph_paths"] = [
        compact_candidate["retrieval_provenance"]["graph_paths"][1]
    ]
    dao.search_v4_memory.return_value = [compact_candidate]
    compact_context = await ContextBuilder(dao).build_context(
        tenant_id="tenant-1",
        agent_id="agent-1",
        dataset_ids=["dataset-1"],
        query="target",
        token_budget=2048,
    )
    assert compact_context["canonical_memories"]
    assert 0 < compact_context["actual_token_count"] <= 2048

    dao.search_v4_memory.return_value = candidates

    context = await ContextBuilder(dao).build_context(
        tenant_id="tenant-1",
        agent_id="agent-1",
        dataset_ids=["dataset-1"],
        query="target",
        token_budget=2048,
    )

    assert len(context["_debug_raw_retrieval"]) == 4
    assert context["canonical_memories"]
    assert context["formatted_context"]
    assert context["actual_token_count"] > 0
    assert context["actual_token_count"] <= 2048

    retained = context["canonical_memories"][0]
    assert retained["assertion_id"] == "target-0"
    assert retained["retrieval_provenance"]["graph_path_id"] == "path-short-0"
    assert retained["retrieval_provenance"]["graph_path_assertion_ids"] == [
        "short-bridge-0",
        "target-0",
    ]
    assert {fact["assertion_id"] for fact in retained["provenance"]} == {
        "short-bridge-0",
        "target-0",
    }
    assert context["context_status"] == "CONTEXT_BUILT_SUCCESSFULLY"
    assert context["context_diagnostics"]["compacted_graph_proof_count"] >= 1


@pytest.mark.asyncio
async def test_graph_chain_budget_counts_evidence_not_internal_path_metadata():
    """Opaque path identifiers must not evict graph-retrieved target evidence."""
    context, _ = await _build_context(_graph_chain_candidates(), token_budget=1000)

    assert {
        memory["entity"]["canonical_name"] for memory in context["canonical_memories"]
    } == {"Alice", "HeliosDB"}
    assert "Alice" in context["formatted_context"]
    assert "Aurora" in context["formatted_context"]
    assert "HeliosDB" in context["formatted_context"]
    assert "graph_path_id" not in context["formatted_context"]
    helios = next(
        memory
        for memory in context["canonical_memories"]
        if memory["entity"]["canonical_name"] == "HeliosDB"
    )
    assert helios["retrieval_provenance"]["graph_paths"] == [
        _graph_chain_candidates()[1]["retrieval_provenance"]["graph_paths"][0]
    ]
    assert context["actual_token_count"] <= 1000


@pytest.mark.asyncio
async def test_non_graph_evidence_that_fits_is_unchanged():
    candidate = {
        "entity": {"canonical_name": "Non Graph"},
        "candidate_id": "non-graph-1",
        "evidence_id": "non-graph-assertion-1",
        "assertion_id": "non-graph-assertion-1",
        "provenance": [
            _assertion(
                "non-graph-assertion-1",
                subject="Subject",
                predicate="states",
                value="Object",
                evidence_span="Ordinary lexical evidence.",
            )
        ],
        "retrieval_provenance": {
            "origins": ["bm25"],
            "lane_ranks": {"bm25": 1},
        },
    }

    context, _ = await _build_context([candidate], token_budget=2048)

    assert context["context_status"] == "CONTEXT_BUILT_SUCCESSFULLY"
    assert len(context["canonical_memories"]) == 1
    assert context["canonical_memories"][0]["provenance"] == [
        {
            "predicate": "states",
            "value": "Object",
            "direction": "forward",
            "subject": "Subject",
            "source_ref": "source:non-graph-assertion-1",
            "document_id": "document-1",
            "revision_id": "revision-1",
            "chunk_id": "chunk:non-graph-assertion-1",
            "evidence_span": "Ordinary lexical evidence.",
            "jurisdiction": "TR",
            "authority_level": "primary",
        }
    ]
    assert context["actual_token_count"] <= 2048


@pytest.mark.asyncio
async def test_atomic_graph_proof_that_fits_retains_all_paths():
    candidate = _historical_shape_candidate(0)
    for assertion in candidate["provenance"]:
        assertion["evidence_span"] = "Concise complete evidence."

    context, _ = await _build_context([candidate], token_budget=10000)

    retained = context["canonical_memories"][0]
    assert len(retained["provenance"]) == 4
    assert len(retained["retrieval_provenance"]["graph_paths"]) == 2
    assert "graph_compaction" not in retained["retrieval_provenance"]
    assert context["context_diagnostics"]["compacted_graph_proof_count"] == 0


@pytest.mark.asyncio
async def test_shortest_path_uses_stable_graph_path_id_tie_break():
    candidate = _historical_shape_candidate(0)
    candidate["provenance"] = [
        candidate["provenance"][0],
        candidate["provenance"][1],
        candidate["provenance"][2],
    ]
    candidate["provenance"][1]["evidence_span"] = "Z bridge detail. " * 40
    candidate["provenance"][2]["evidence_span"] = "A bridge detail. " * 40
    candidate["retrieval_provenance"]["graph_paths"] = [
        {
            "graph_path_id": "path-z",
            "assertion_ids": ["short-bridge-0", "target-0"],
            "entity_ids": ["seed-z", "bridge-z", "target-0"],
            "edge_directions": ["forward", "forward"],
            "predicates": ["supports", "establishes"],
            "seed_id": "seed-z",
        },
        {
            "graph_path_id": "path-a",
            "assertion_ids": ["long-bridge-a-0", "target-0"],
            "entity_ids": ["seed-a", "bridge-a", "target-0"],
            "edge_directions": ["forward", "forward"],
            "predicates": ["alternative_support", "establishes"],
            "seed_id": "seed-a",
        },
    ]

    context, _ = await _build_context([candidate], token_budget=2200)

    retained = context["canonical_memories"][0]
    assert retained["retrieval_provenance"]["graph_path_id"] == "path-a"
    assert [fact["assertion_id"] for fact in retained["provenance"]] == [
        "long-bridge-a-0",
        "target-0",
    ]


@pytest.mark.asyncio
async def test_minimum_complete_proof_exceeding_budget_fails_closed_explicitly():
    candidate = _historical_shape_candidate(0)
    candidate["provenance"][0]["evidence_span"] = "required target text " * 500

    context, _ = await _build_context([candidate], token_budget=1024)

    assert context["canonical_memories"] == []
    assert context["formatted_context"] == ""
    assert context["actual_token_count"] == 0
    assert context["context_status"] == "VALID_EVIDENCE_EXCEEDS_CONTEXT_BUDGET"
    assert context["context_diagnostics"]["budget_rejections"] == [
        {
            "candidate_id": "target-0",
            "reason": "PROOF_EXCEEDS_CONTEXT_BUDGET",
        }
    ]


@pytest.mark.asyncio
async def test_compacted_proof_preserves_auditable_provenance_and_scope():
    context, _ = await _build_context(
        [_historical_shape_candidate(0)], token_budget=2048
    )

    retained = context["canonical_memories"][0]
    assert retained["scope_identity"] == {
        "tenant_id": "tenant-1",
        "dataset_id": "dataset-1",
        "agent_id": "agent-1",
        "jurisdiction": "TR",
        "status": "ACTIVE",
    }
    for fact in retained["provenance"]:
        assert fact["assertion_id"]
        assert fact["source_ref"]
        assert fact["document_id"] == "document-1"
        assert fact["revision_id"] == "revision-1"
        assert fact["chunk_id"]
        assert fact["evidence_span"]
        assert fact["jurisdiction"] == "TR"
        assert fact["authority_level"] == "primary"


@pytest.mark.asyncio
async def test_scope_arguments_reach_retrieval_and_filtered_empty_stays_empty():
    context, dao = await _build_context([], token_budget=2048)

    dao.search_v4_memory.assert_awaited_once_with(
        tenant_id="tenant-1",
        agent_id="agent-1",
        dataset_ids=["dataset-1"],
        query="target",
        limit=20,
        jurisdiction=None,
        valid_at=None,
        valid_from=None,
        valid_to=None,
    )
    assert context["canonical_memories"] == []
    assert context["context_status"] == "NO_RETRIEVAL_EVIDENCE"


@pytest.mark.asyncio
async def test_non_graph_fact_trimming_remains_ranked_and_budget_bounded():
    candidate = {
        "entity": {"canonical_name": "Budgeted Non Graph"},
        "candidate_id": "non-graph-1",
        "evidence_id": "kept",
        "assertion_id": "kept",
        "provenance": [
            _assertion(
                "kept",
                subject="Subject",
                predicate="first",
                value="kept value",
                evidence_span="Short evidence.",
            ),
            _assertion(
                "trimmed",
                subject="Subject",
                predicate="second",
                value="trimmed value",
                evidence_span="oversized trailing evidence " * 200,
            ),
        ],
        "retrieval_provenance": {
            "origins": ["bm25"],
            "lane_ranks": {"bm25": 1},
        },
    }

    context, _ = await _build_context([candidate], token_budget=900)

    assert len(context["canonical_memories"]) == 1
    assert [
        fact["predicate"] for fact in context["canonical_memories"][0]["provenance"]
    ] == ["first"]
    assert context["actual_token_count"] <= 900


@pytest.mark.asyncio
async def test_graph_compaction_is_deterministic():
    candidates = [_historical_shape_candidate(index) for index in range(3)]

    first, _ = await _build_context(copy.deepcopy(candidates), token_budget=2048)
    second, _ = await _build_context(copy.deepcopy(candidates), token_budget=2048)

    for key in (
        "canonical_memories",
        "formatted_context",
        "actual_token_count",
        "context_status",
        "context_diagnostics",
    ):
        assert first[key] == second[key]
