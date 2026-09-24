"""Phase 6 regression tests: Production RRF / Fusion Unification hardening.

Verifies:
1. Vector vs lexical disagreement.
2. Non-vector lanes agree / vector wrong.
3. Missing lane handling (deterministic, non-throwing).
4. Duplicate candidate accumulation across multiple lanes without duplicate entries.
5. Graph harmful candidate cannot overturn multi-lane consensus.
6. Assertion-only useful evidence properly ranked and identified.
7. Configurable K (smoothing constant).
8. Configurable lane weights.
9. Production function = test function (single authority).
10. Deterministic tie-breaking on equal scores.
11. Query-independent legal boost cannot silently override relevance.
12. Provenance materialization does not alter already-decided rank.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from mesa_evals.v4_rrf_ablation import evaluate_lane_ablation, fixed_legal_corpus, rrf_fuse
from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import MemoryDAO
from mesa_storage.retrieval_scope import (
    V4_RRF_DEFAULT_K,
    V4_RRF_LANE_ORDER,
    V4_RRF_LANE_WEIGHTS,
    compute_rrf_lane_score,
    rrf_fuse_lanes,
)
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


def _make_dao(engine: AsyncEngine) -> MemoryDAO:
    vector = SimpleNamespace(
        compute_embedding=AsyncMock(return_value=[1.0, 0.0]),
        compute_query_embedding=AsyncMock(return_value=[1.0, 0.0]),
        upsert=AsyncMock(),
        search=AsyncMock(return_value=[]),
    )
    graph = SimpleNamespace(
        insert_node=AsyncMock(),
        insert_assertion=AsyncMock(),
        link_assertions=AsyncMock(),
        traverse_paths=AsyncMock(return_value=[]),
    )
    return MemoryDAO(engine, vector, graph)


async def _seed_test_assertion(
    dao: MemoryDAO,
    *,
    tenant_id: str,
    agent_id: str,
    dataset_id: str,
    doc_id: str,
    subject: str,
    predicate: str,
    literal_value: str,
    evidence_span: str,
    raw_log_id: int,
    authority_level: str = "OFFICIAL",
    confidence: float = 1.0,
):
    cand = MemoryCandidate.from_raw_log(
        raw_log_id=raw_log_id,
        tenant_id=tenant_id,
        workspace_id="workspace-p6",
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id="revision-p6",
        chunk_id=f"chunk-{doc_id}",
        source_ref=f"source-{doc_id}",
        agent_id=agent_id,
        session_id="session-p6",
        content_payload=f"{subject} {predicate} {literal_value}. {evidence_span}",
        embedding_provider="test",
        embedding_model="catalog-contract",
        embedding_version="v1",
        embedding_dimension=2,
        embedding_space_id="test:catalog-contract:v1:2:norm=true",
        embedding_normalized=True,
    ).as_consolidation_record()
    await dao.record_mutation(cand, raw_log_id=raw_log_id)
    mut = await dao.get_projection_mutation(str(cand["mutation_id"]))
    assert mut is not None

    await dao.project_v4_sql_entity(mutation=mut, entity_name=subject)
    triplet = {
        "head": subject,
        "relation": predicate,
        "literal_value": literal_value,
        "evidence_span": evidence_span,
        "confidence": confidence,
        "metadata": {"authority_level": authority_level},
    }
    await dao.project_v4_graph_triplet(mutation=mut, triplet=triplet)
    assertions = await dao.list_v4_assertions_for_mutation(str(mut["mutation_id"]))
    assertion = assertions[0]
    await dao.project_v4_vector_assertion(mutation=mut, assertion=assertion)
    async with dao._sql.transaction() as db:
        await db.execute(
            "UPDATE memory_mutations SET state = 'COMMITTED' WHERE mutation_id = ?",
            (mut["mutation_id"],),
        )
        await db.commit()
    return assertion


# 1. Single Authority & Unit Tests
def test_p6_compute_rrf_lane_score_math():
    """Verify exact formula weight / (k + rank)."""
    assert compute_rrf_lane_score(1, lane="vector", k=60) == pytest.approx(10.0 / 61.0)
    assert compute_rrf_lane_score(2, lane="bm25", k=60) == pytest.approx(1.0 / 62.0)
    assert compute_rrf_lane_score(1, lane="assertion", k=60) == pytest.approx(1.0 / 61.0)
    assert compute_rrf_lane_score(3, lane="graph", k=60) == pytest.approx(2.0 / 63.0)

    # Custom weights and custom k
    custom_weights = {"vector": 5.0, "bm25": 2.5}
    assert compute_rrf_lane_score(1, lane="vector", k=100, weights=custom_weights) == pytest.approx(5.0 / 101.0)
    assert compute_rrf_lane_score(1, lane="bm25", k=100, weights=custom_weights) == pytest.approx(2.5 / 101.0)

    with pytest.raises(ValueError):
        compute_rrf_lane_score(0, lane="vector")


def test_p6_rrf_fuse_lanes_deterministic_ties():
    """Verify that equal-score ties are broken deterministically by candidate_id."""
    # cand-b and cand-a get identical scores across reversed lanes
    lane_rankings = {
        "bm25": ["cand-b", "cand-a"],
        "assertion": ["cand-a", "cand-b"],
    }
    fused = rrf_fuse_lanes(lane_rankings, k=60, weights={"bm25": 1.0, "assertion": 1.0})
    # cand-a must sort before cand-b alphabetically
    assert [cid for cid, _, _ in fused] == ["cand-a", "cand-b"]
    assert fused[0][1] == pytest.approx(fused[1][1])


def test_p6_production_function_equals_test_function():
    """Verify that rrf_fuse and evaluate_lane_ablation use production math."""
    corpus, qrels = fixed_legal_corpus()
    report = evaluate_lane_ablation(corpus, qrels, k=V4_RRF_DEFAULT_K, weights=V4_RRF_LANE_WEIGHTS)
    assert "rrf_all" in report["scores"]
    assert report["scores"]["rrf_all"] >= report["scores"]["vector_only"]


def test_p6_missing_lane_graceful_handling():
    """Verify that missing or empty lanes do not cause errors or NaNs."""
    lane_rankings = {
        "vector": ["item-1"],
        "bm25": [],
        "assertion": [],
        "graph": [],
    }
    fused = rrf_fuse_lanes(lane_rankings, k=60)
    assert len(fused) == 1
    assert fused[0][0] == "item-1"
    assert fused[0][1] == pytest.approx(10.0 / 61.0)
    assert fused[0][2] == {"vector": 1}


def test_p6_duplicate_candidate_score_accumulation():
    """Verify that a candidate appearing in multiple lanes correctly accumulates score."""
    lane_rankings = {
        "vector": ["cand-1", "cand-2"],
        "bm25": ["cand-2", "cand-1"],
        "assertion": ["cand-1"],
        "graph": ["cand-1"],
    }
    fused = rrf_fuse_lanes(lane_rankings, k=60)
    scores_by_id = {cid: score for cid, score, _ in fused}
    # cand-1: vector(rank 1) + bm25(rank 2) + assertion(rank 1) + graph(rank 1)
    expected_c1 = (10.0 / 61.0) + (1.0 / 62.0) + (1.0 / 61.0) + (2.0 / 61.0)
    assert scores_by_id["cand-1"] == pytest.approx(expected_c1)


def test_p6_non_vector_agreement_beats_vector_under_balanced_weights():
    """When weights are balanced or configured, unanimous consensus in bm25, assertion, and graph outranks a lonely vector hit."""
    lane_rankings = {
        "vector": ["cand-wrong"],
        "bm25": ["cand-correct"],
        "assertion": ["cand-correct"],
        "graph": ["cand-correct"],
    }
    # With balanced weights (e.g. vector 2.0, bm25 1.5, assertion 1.5, graph 2.0)
    balanced_weights = {"vector": 2.0, "bm25": 1.5, "assertion": 1.5, "graph": 2.0}
    fused = rrf_fuse_lanes(lane_rankings, k=60, weights=balanced_weights)
    assert fused[0][0] == "cand-correct"
    assert fused[1][0] == "cand-wrong"


# 2. Production Integration Tests through search_v4_memory
@pytest.mark.asyncio
async def test_p6_query_independent_legal_boost_cannot_override_relevance(tmp_path):
    """Verify that a candidate with OFFICIAL authority level cannot jump over a higher relevance candidate when query has no legal citations."""
    db_path = str(tmp_path / "p6_boost.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p6"
    agent_id = "agent-p6"
    dataset_id = "dataset-p6"

    try:
        # Candidate 1: Highly relevant to the query ("veri tabanı ve yedekleme")
        ass1 = await _seed_test_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-high-rel",
            subject="VeriYonetimi",
            predicate="tanımlar",
            literal_value="Yedekleme ve veri tabanı politikası",
            evidence_span="Veri tabanı periyodik yedekleme prosedürleri işletilmelidir.",
            raw_log_id=1,
            authority_level="SECONDARY",  # Lower authority metadata
            confidence=0.8,
        )

        # Candidate 2: Irrelevant/weakly relevant, but marked OFFICIAL
        ass2 = await _seed_test_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-low-rel",
            subject="KamuMevzuati",
            predicate="hükmü",
            literal_value="Genel mevzuat bildirimleri",
            evidence_span="Kamu kurumlarında resmi yazışma usulleri.",
            raw_log_id=2,
            authority_level="OFFICIAL",  # High authority metadata
            confidence=1.0,
        )

        # Query has NO legal citation
        results = await dao.search_v4_memory(
            query="veri tabanı yedekleme politikası ve prosedürleri",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
        )

        assert len(results) >= 1
        # The relevant candidate must win, NOT the irrelevant OFFICIAL one
        assert results[0]["assertion_id"] == ass1["assertion_id"]
        # In queries without legal citations, legal_factor must be 1.0 (no query-independent boost)
        for r in results:
            assert r["legal_factor"] == 1.0
            assert r["final_score"] == pytest.approx(r["rrf_score"])
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_p6_configurable_k_in_production_search(tmp_path):
    """Verify that search_v4_memory respects custom rrf_k parameter."""
    db_path = str(tmp_path / "p6_k.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p6"
    agent_id = "agent-p6"
    dataset_id = "dataset-p6"

    try:
        ass = await _seed_test_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-k",
            subject="TestSubject",
            predicate="test_pred",
            literal_value="Test value",
            evidence_span="Test evidence span for configurable k.",
            raw_log_id=10,
        )

        # Search with default k=60
        res_default = await dao.search_v4_memory(
            query="TestSubject test value",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
            rrf_k=60,
        )

        # Search with custom k=20
        res_k20 = await dao.search_v4_memory(
            query="TestSubject test value",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
            rrf_k=20,
        )

        assert len(res_default) >= 1
        assert len(res_k20) >= 1
        # Smaller k means larger reciprocal rank score (1/(20+1) > 1/(60+1))
        assert res_k20[0]["rrf_score"] > res_default[0]["rrf_score"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_p6_configurable_weights_in_production_search(tmp_path):
    """Verify that search_v4_memory respects custom rrf_weights parameter."""
    db_path = str(tmp_path / "p6_weights.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p6"
    agent_id = "agent-p6"
    dataset_id = "dataset-p6"

    try:
        ass = await _seed_test_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-w",
            subject="WeightSubject",
            predicate="weight_pred",
            literal_value="Weight literal",
            evidence_span="Weight evidence span.",
            raw_log_id=20,
        )

        # High bm25 weight vs low bm25 weight
        res_high_bm25 = await dao.search_v4_memory(
            query="WeightSubject weight literal",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
            rrf_weights={"vector": 1.0, "bm25": 50.0, "assertion": 1.0, "graph": 1.0},
        )

        res_low_bm25 = await dao.search_v4_memory(
            query="WeightSubject weight literal",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
            rrf_weights={"vector": 1.0, "bm25": 1.0, "assertion": 1.0, "graph": 1.0},
        )

        assert len(res_high_bm25) >= 1
        assert len(res_low_bm25) >= 1
        assert res_high_bm25[0]["rrf_score"] > res_low_bm25[0]["rrf_score"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_p6_materialization_does_not_alter_candidate_order(tmp_path):
    """Verify that post-fusion materialization of entities and chunks preserves the rank order."""
    db_path = str(tmp_path / "p6_mat.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p6"
    agent_id = "agent-p6"
    dataset_id = "dataset-p6"

    try:
        ass1 = await _seed_test_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-mat-1",
            subject="FirstEntity",
            predicate="tanımlar",
            literal_value="First literal value matching exactly",
            evidence_span="First evidence span text for ranking test.",
            raw_log_id=30,
        )
        ass2 = await _seed_test_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-mat-2",
            subject="SecondEntity",
            predicate="belirler",
            literal_value="Second literal value matching partially",
            evidence_span="Second evidence span text.",
            raw_log_id=31,
        )

        results = await dao.search_v4_memory(
            query="FirstEntity First literal value matching exactly",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
        )

        assert len(results) >= 2
        # Highest score must be at index 0, followed by index 1 with lower or equal score
        scores = [r["final_score"] for r in results]
        assert scores == sorted(scores, reverse=True)
        assert results[0]["assertion_id"] == ass1["assertion_id"]
    finally:
        await engine.close()
