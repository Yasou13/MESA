"""Phase 1 Regression Tests: Evidence-Level Retrieval Contract.

Verifies:
1. Matched evidence specificity (source_chunk_id and assertion_id preserved).
2. Entity with 100 chunks does NOT pollute Top-1 provenance with unrelated chunks.
3. Vector raw score / distance preservation.
4. Endpoint expansion does not displace real vector hits.
5. Backward compatibility of public entity and score fields.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


async def _create_committed_mutation(
    dao: MemoryDAO,
    *,
    raw_log_id: int,
    tenant_id: str,
    dataset_id: str,
    agent_id: str,
    chunk_id: str,
    content: str,
    subject: str,
    predicate: str = "regulates_article",
    object_value: str | None = None,
    literal_value: str | None = None,
    document_id: str = "doc-1",
    evidence_span: str = "",
) -> dict:
    cand = MemoryCandidate.from_raw_log(
        raw_log_id=raw_log_id,
        tenant_id=tenant_id,
        workspace_id="workspace-default",
        dataset_id=dataset_id,
        document_id=document_id,
        revision_id="revision-default",
        chunk_id=chunk_id,
        source_ref=f"source-{chunk_id}",
        agent_id=agent_id,
        session_id="session-default",
        content_payload=content,
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

    # Project entity
    await dao.project_v4_sql_entity(mutation=mut, entity_name=subject)
    if object_value:
        await dao.project_v4_sql_entity(mutation=mut, entity_name=object_value)

    # Project assertion via triplet
    triplet = {
        "head": subject,
        "relation": predicate,
    }
    if object_value:
        triplet["tail"] = object_value
    else:
        triplet["literal_value"] = literal_value or f"value-{chunk_id}"
    if evidence_span:
        triplet["evidence_span"] = evidence_span

    await dao.project_v4_graph_triplet(mutation=mut, triplet=triplet)
    assertions = await dao.list_v4_assertions_for_mutation(str(mut["mutation_id"]))
    assert len(assertions) >= 1
    assertion = assertions[0]

    # Project vector assertion
    await dao.project_v4_vector_assertion(mutation=mut, assertion=assertion)

    # Commit mutation
    async with dao._sql.transaction() as db:
        await db.execute(
            "UPDATE memory_mutations SET state = 'COMMITTED' WHERE mutation_id = ?",
            (mut["mutation_id"],),
        )
        await db.commit()

    return assertion


@pytest.mark.asyncio
async def test_phase1_evidence_level_specificity_and_provenance_isolation(tmp_path):
    """Test 1-4 & 5-6:

    1 entity with 100 different chunks.
    Query matches only chunk-73.
    Retrieval must preserve chunk-73 as matched evidence.
    Unmatched chunks must NOT appear in matched provenance.
    Vector distance must be preserved.
    """
    db_path = str(tmp_path / "phase1_test.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    vector = SimpleNamespace(
        compute_embedding=AsyncMock(return_value=[1.0, 0.0]),
        compute_query_embedding=AsyncMock(return_value=[1.0, 0.0]),
        upsert=AsyncMock(),
        search=AsyncMock(),
    )
    graph = SimpleNamespace(
        insert_node=AsyncMock(),
        insert_assertion=AsyncMock(),
        link_assertions=AsyncMock(),
    )

    dao = MemoryDAO(engine, vector, graph)
    tenant_id = "tenant-p1"
    agent_id = "agent-p1"
    dataset_id = "dataset-p1"

    try:
        # Ingest 100 assertions for "Türk Borçlar Kanunu", each with a different chunk_id
        chunk_73_aid = None
        for i in range(1, 101):
            chunk_name = f"chunk-{i}"
            assertion = await _create_committed_mutation(
                dao,
                raw_log_id=100 + i,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                agent_id=agent_id,
                chunk_id=chunk_name,
                content=f"TBK madde {i} hukumleri...",
                subject="Türk Borçlar Kanunu",
                predicate="regulates_article",
                literal_value=f"Madde {i}",
                evidence_span=f"Madde {i} ayrintili metin...",
            )
            if i == 73:
                chunk_73_aid = assertion["assertion_id"]

        assert chunk_73_aid is not None

        # Mock vector search to return ONLY chunk-73's assertion with raw distance 0.042
        vector.search.return_value = [{"node_id": chunk_73_aid, "_distance": 0.042}]

        results = await dao.search_v4_memory(
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            query="TBK madde 73 kusur sorumlulugu",
            limit=10,
        )

        assert len(results) >= 1, "Expected at least 1 retrieval result"
        top1 = results[0]

        # 1. Evidence identity preserved
        assert top1.get("source_chunk_id") == "chunk-73"
        assert top1.get("assertion_id") == chunk_73_aid

        # 2. Raw score / distance preserved
        assert "retrieval_provenance" in top1
        raw_scores = top1["retrieval_provenance"].get("raw_scores", {})
        assert "vector" in raw_scores
        assert abs(raw_scores["vector"] - 0.042) < 1e-5

        # 3. Provenance specificity: ONLY chunk-73 in matched provenance!
        # Other 99 chunks must NOT pollute Top-1 provenance!
        prov_chunks = [p.get("chunk_id") for p in top1["provenance"]]
        assert "chunk-73" in prov_chunks
        assert (
            len(prov_chunks) == 1
        ), f"Expected exactly 1 matched chunk in Top-1 provenance, but found {len(prov_chunks)}: {prov_chunks[:5]}..."
        assert "chunk-1" not in prov_chunks
        assert "chunk-12" not in prov_chunks
        assert "chunk-99" not in prov_chunks

        # 4. Backward compatibility
        assert top1["entity"]["canonical_name"] == "Türk Borçlar Kanunu"
        assert "rrf_score" in top1
        assert "final_score" in top1

    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_phase1_endpoint_expansion_does_not_displace_vector_rank(tmp_path):
    """Test 7: Subject/object endpoint expansion must NOT displace real vector hits.

    Assertion 1: S1 -> O1 (distance 0.01)
    Assertion 2: S2 -> O2 (distance 0.02)
    Vector search returns [Assertion 1, Assertion 2].
    Rank 1 must be Assertion 1 (or S1).
    Rank 2 must be Assertion 2 (or S2), NOT O1!
    """
    db_path = str(tmp_path / "phase1_test2.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    vector = SimpleNamespace(
        compute_embedding=AsyncMock(return_value=[1.0, 0.0]),
        compute_query_embedding=AsyncMock(return_value=[1.0, 0.0]),
        upsert=AsyncMock(),
        search=AsyncMock(),
    )
    graph = SimpleNamespace(
        insert_node=AsyncMock(),
        insert_assertion=AsyncMock(),
        link_assertions=AsyncMock(),
    )

    dao = MemoryDAO(engine, vector, graph)
    tenant_id = "tenant-p1"
    agent_id = "agent-p1"
    dataset_id = "dataset-p1"

    try:
        a1 = await _create_committed_mutation(
            dao,
            raw_log_id=1,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            agent_id=agent_id,
            chunk_id="chunk-a1",
            content="SubjectOne relates to ObjectOne",
            subject="SubjectOne",
            predicate="relates_to",
            object_value="ObjectOne",
        )
        a2 = await _create_committed_mutation(
            dao,
            raw_log_id=2,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            agent_id=agent_id,
            chunk_id="chunk-a2",
            content="SubjectTwo relates to ObjectTwo",
            subject="SubjectTwo",
            predicate="relates_to",
            object_value="ObjectTwo",
        )

        vector.search.return_value = [
            {"node_id": a1["assertion_id"], "_distance": 0.01},
            {"node_id": a2["assertion_id"], "_distance": 0.02},
        ]

        results = await dao.search_v4_memory(
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            query="test relations",
            limit=5,
        )

        assert len(results) >= 2
        # Rank 1 must be Assertion 1 / SubjectOne
        assert results[0].get("source_chunk_id") == "chunk-a1"
        assert results[0]["entity"]["canonical_name"] == "SubjectOne"

        # Rank 2 MUST be Assertion 2 / SubjectTwo, NOT ObjectOne!
        assert results[1].get("source_chunk_id") == "chunk-a2"
        assert results[1]["entity"]["canonical_name"] == "SubjectTwo"

    finally:
        await engine.close()
