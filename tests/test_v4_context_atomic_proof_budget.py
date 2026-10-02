import copy
from unittest.mock import AsyncMock

import pytest

from mesa_memory.context_builder import ContextBuilder


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


def _historical_shape_candidate(index: int) -> dict[str, object]:
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


@pytest.mark.asyncio
async def test_historical_shape_keeps_minimum_complete_graph_proof(monkeypatch):
    """A compactable valid proof must not become zero evidence at 2048 tokens."""
    monkeypatch.setattr(
        "mesa_memory.context_builder._count_tokens",
        lambda text: len(text.encode("utf-8")),
    )
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
    assert {
        fact["assertion_id"] for fact in retained["provenance"]
    } == {"short-bridge-0", "target-0"}
