"""Test SQLite FTS5 / BM25 scope isolation under cross-scope corpus insertion.

Verifies T04 (A-06):
1. No unauthorized rows/identities/statistics leak across tenants/datasets/agents.
2. Evaluates candidate set, rank order, and final retrieval responses for Tenant A before and after Tenant B adds confounding documents.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_v4_retrieval_hardening_phase1 import _create_committed_mutation

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


async def _environment(tmp_path):
    sql = AsyncEngine(str(tmp_path / "bm25_isolation.sqlite"))
    await sql.initialize()
    await initialize_schema(sql)
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
        search_v4_graph=AsyncMock(return_value=[]),
        is_operational=True,
    )
    return sql, vector, graph, MemoryDAO(sql, vector, graph)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "foreign_scope",
    [
        ("tenant-b", "agent-b", "dataset-b"),
        ("tenant-a", "agent-a", "dataset-b"),
        ("tenant-a", "agent-b", "dataset-a"),
    ],
)
async def test_fts_bm25_scope_isolation_under_cross_tenant_corpus_load(tmp_path, foreign_scope):
    sql, _vector, _graph, dao = await _environment(tmp_path)
    try:
        # 1. Populate Tenant A with policies
        a1 = await _create_committed_mutation(
            dao,
            raw_log_id=1,
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_id="dataset-a",
            chunk_id="chunk-a1",
            content="Article 12: Data protection officer must oversee compliance and privacy audits.",
            subject="Article 12",
            predicate="specifies_duty",
            object_value="Data protection officer",
            evidence_span="Data protection officer must oversee compliance and privacy audits.",
        )
        await _create_committed_mutation(
            dao,
            raw_log_id=2,
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_id="dataset-a",
            chunk_id="chunk-a2",
            content="Article 15: Subject access request responses must be delivered within thirty days.",
            subject="Article 15",
            predicate="regulates_timeline",
            object_value="Subject access request",
            evidence_span="Subject access request responses must be delivered within thirty days.",
        )
        await _create_committed_mutation(
            dao,
            raw_log_id=3,
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_id="dataset-a",
            chunk_id="chunk-a3",
            content="Article 20: Right to data portability between cloud services and platforms.",
            subject="Article 20",
            predicate="grants_right",
            object_value="Data portability",
            evidence_span="Right to data portability between cloud services and platforms.",
        )

        query = "Data protection officer compliance"
        meta_before = {}
        results_before = await dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query=query,
            certification_metadata=meta_before,
        )

        # Confirm baseline sanity
        assert len(results_before) > 0
        top_aid_before = results_before[0]["assertion_id"]
        assert top_aid_before == a1["assertion_id"]
        before_ranks = [r["assertion_id"] for r in results_before]

        # 2. Populate Tenant B with 40 mutations heavily skewing term frequencies for query terms
        foreign_assertion_ids = []
        for i in range(10, 50):
            foreign = await _create_committed_mutation(
                dao,
                raw_log_id=i,
                tenant_id=foreign_scope[0],
                agent_id=foreign_scope[1],
                dataset_id=foreign_scope[2],
                chunk_id=f"chunk-b-{i}",
                content=f"Secret tenant-b report #{i}: Data protection officer officer officer compliance compliance override.",
                subject=f"Secret Tenant B #{i}",
                predicate="overrides",
                object_value="Compliance officer",
                evidence_span="Data protection officer officer officer compliance compliance override.",
            )
            foreign_assertion_ids.append((foreign["assertion_id"],))

        # 3. Query Tenant A again with identical query
        meta_after = {}
        results_after = await dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query=query,
            certification_metadata=meta_after,
        )

        # Verifications
        # 1. Zero leakage: absolutely no results from tenant-b
        for r in results_after:
            assert r["scope_identity"]["tenant_id"] == "tenant-a"
            assert r["scope_identity"]["dataset_id"] == "dataset-a"
            assert r["scope_identity"]["agent_id"] == "agent-a"
            assert "tenant-b" not in repr(r)
            assert "Secret" not in repr(r)

        # 2. Candidate set and rank order for Tenant A
        after_ranks = [r["assertion_id"] for r in results_after]
        assert after_ranks == before_ranks, f"Rank order changed! Before: {before_ranks}, After: {after_ranks}"

        # 3. Final scores and diagnostics comparison
        before_scores = [(r["assertion_id"], r["rrf_score"], r["final_score"]) for r in results_before]
        after_scores = [(r["assertion_id"], r["rrf_score"], r["final_score"]) for r in results_after]
        assert after_scores == before_scores
        assert [r["retrieval_provenance"]["raw_scores"] for r in results_after] == [r["retrieval_provenance"]["raw_scores"] for r in results_before]

        # Scope audit candidate counts must remain completely identical
        assert meta_after["scope_audit"]["evaluated_candidate_count"] == meta_before["scope_audit"]["evaluated_candidate_count"]
        assert meta_after["scope_audit"]["eligible_candidate_count"] == meta_before["scope_audit"]["eligible_candidate_count"]
        assert meta_after["scope_audit"]["excluded_candidate_count"] == meta_before["scope_audit"]["excluded_candidate_count"]
        assert meta_after["scope_audit"]["exclusion_audit_hash"] == meta_before["scope_audit"]["exclusion_audit_hash"]
        async with sql.transaction() as db:
            await db.executemany("DELETE FROM v4_assertions WHERE assertion_id = ?", foreign_assertion_ids)
            await db.commit()
        meta_deleted = {}
        assert await dao.search_v4_memory(
            tenant_id="tenant-a", agent_id="agent-a", dataset_ids=["dataset-a"], query=query,
            certification_metadata=meta_deleted,
        ) == results_before
        assert meta_deleted == meta_before
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_zero_confidence_assertion_preserves_zero(tmp_path):
    sql, _vector, _graph, dao = await _environment(tmp_path)
    try:
        cand = await _create_committed_mutation(
            dao,
            raw_log_id=1,
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_id="dataset-a",
            chunk_id="chunk-1",
            content="Policy with zero confidence test.",
            subject="Policy",
            predicate="tests",
            object_value="Zero",
        )
        async with sql.transaction() as db:
            await db.execute(
                "UPDATE v4_assertions SET confidence = 0.0 WHERE assertion_id = ?",
                (cand["assertion_id"],),
            )
            await db.commit()

        results = await dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="Policy zero",
        )
        assert len(results) == 1
        assert results[0]["matched_assertions"][0]["confidence"] == 0.0
    finally:
        await sql.close()

