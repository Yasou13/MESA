"""Phase 2 regression tests: Vector / Assertion Representation hardening.

Verifies:
1. Ingesting 8 assertions from one chunk produces distinct, meaningful assertion vector representations.
2. Different facts have distinct vector payloads rather than N identical copies of chunk_text[:2000].
3. Tail evidence beyond character 2000 in the chunk is preserved in assertion representations.
4. ANN raw distance is preserved in search candidate results.
5. Asymmetric embedding roles: document/passage role for assertion projection, query role for search.
6. 2048 dimension fence.
7. Projection idempotency.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import V4_VECTOR_REPRESENTATION_VERSION, MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine


class MockEmbeddingService:
    def __init__(self, dimension: int = 4):
        self.dimension = dimension
        self.recorded_doc_calls: list[str] = []
        self.recorded_query_calls: list[str] = []
        from mesa_memory.embedding.service import EmbeddingIdentity

        self._identity = EmbeddingIdentity(
            provider="mock",
            model="mock-model",
            version="v1",
            dimension=dimension,
            normalized=True,
            model_revision=None,
        )

    def identity(self):
        return self._identity

    async def aembed_document(self, text: str) -> list[float]:
        self.recorded_doc_calls.append(text)
        val = (
            float(int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16) % 1000)
            / 1000.0
        )
        return [val, float(len(text)), 0.5, 1.0][: self.dimension]

    async def aembed_query(self, text: str) -> list[float]:
        self.recorded_query_calls.append(text)
        val = (
            float(int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16) % 1000)
            / 1000.0
        )
        return [val, float(len(text)), 0.5, 1.0][: self.dimension]

    def embed_document(self, text: str) -> list[float]:
        self.recorded_doc_calls.append(text)
        val = (
            float(int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16) % 1000)
            / 1000.0
        )
        return [val, float(len(text)), 0.5, 1.0][: self.dimension]


@pytest.mark.asyncio
async def test_phase2_multi_assertion_chunk_representation(tmp_path):
    """Test that 8 assertions from 1 chunk produce distinct vector payloads and preserve tail evidence."""
    db_path = str(tmp_path / "mesa_test_p2.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)
    vec_path = tmp_path / "vec_p2"
    vec = VectorEngine(uri=str(vec_path))
    await vec.initialize()

    mock_emb = MockEmbeddingService(dimension=4)
    vec._embedding_service = mock_emb

    graph = SimpleNamespace(
        insert_node=AsyncMock(),
        insert_assertion=AsyncMock(),
        link_assertions=AsyncMock(),
        search=AsyncMock(return_value=[]),
        get_existing_node_ids=AsyncMock(return_value=set()),
    )
    dao = MemoryDAO(engine, vec, graph)

    try:
        # Construct a large chunk > 3000 chars
        filler_lead = "Giriş metni ve genel sözleşme açıklamaları. " * 35  # ~1500 chars
        tail_section = (
            " " * 600
            + "KUYRUK_BOLUMU: Madde 8 uyarınca taraflar sözleşmeyi feshedebilir."
        )  # chars 2000+
        chunk_content = filler_lead + tail_section
        assert (
            len(chunk_content) > 2000
        ), f"Chunk length must be > 2000, got {len(chunk_content)}"

        # Create 8 distinct assertions from this chunk
        triplets = []
        for i in range(1, 9):
            if i == 8:
                # Assertion 8 comes from the tail section (>2000 chars)
                triplets.append(
                    {
                        "head": "Sözleşme",
                        "relation": "fesih_hükmü",
                        "tail": "Madde 8",
                        "literal_value": None,
                        "confidence": 1.0,
                        "fact_text": "Madde 8 uyarınca taraflar fesih hakkına sahiptir.",
                        "source_span": "KUYRUK_BOLUMU: Madde 8 uyarınca taraflar sözleşmeyi feshedebilir.",
                    }
                )
            else:
                triplets.append(
                    {
                        "head": "Sözleşme",
                        "relation": f"kural_{i}",
                        "tail": f"Madde_{i}",
                        "literal_value": None,
                        "confidence": 1.0,
                        "fact_text": f"Sözleşme kural {i} detaylı açıklaması.",
                        "source_span": f"Madde {i} hükmü uygulanır.",
                    }
                )

        candidate = MemoryCandidate.from_raw_log(
            raw_log_id=101,
            tenant_id="tenant-test",
            workspace_id="ws-test",
            dataset_id="dataset-test",
            document_id="doc-sozlesme",
            revision_id="rev-1",
            chunk_id="chunk-multi",
            source_ref="ref-1",
            agent_id="agent-p2",
            session_id="session-p2",
            content_payload=chunk_content,
            embedding_provider="mock",
            embedding_model="mock-model",
            embedding_version="v1",
            embedding_dimension=4,
            embedding_space_id="mock:mock-model:v1:4:norm=true",
            embedding_normalized=True,
            validation_mode=0,
        ).as_consolidation_record()

        await dao.record_mutation(candidate, raw_log_id=101)
        await dao.record_mutation_extraction(
            "agent-p2", candidate["mutation_id"], triplets
        )
        await dao.set_mutation_state("agent-p2", candidate["mutation_id"], "VALIDATED")

        mutation = await dao.get_projection_mutation(str(candidate["mutation_id"]))
        assert mutation is not None

        # Project SQL entities and assertions
        for t in triplets:
            await dao.project_v4_sql_entity(mutation=mutation, entity_name=t["head"])
            await dao.project_v4_sql_entity(mutation=mutation, entity_name=t["tail"])
            await dao.project_v4_sql_assertion(mutation=mutation, triplet=t)

        # Now load assertions
        assertions = await dao.list_v4_assertions_for_mutation(
            str(mutation["mutation_id"])
        )
        assert len(assertions) == 8

        # Project vector assertions
        mock_emb.recorded_doc_calls.clear()
        for a in assertions:
            await dao.project_v4_vector_assertion(mutation=mutation, assertion=a)

        assert (
            len(mock_emb.recorded_doc_calls) == 8
        ), "Expected 8 embedding calls for 8 assertions"

        # CRITICAL CHECK 1: The 8 assertions must NOT all have identical payload text!
        unique_payloads = set(mock_emb.recorded_doc_calls)
        assert len(unique_payloads) == 8, (
            f"Expected 8 unique assertion vector payloads, but got {len(unique_payloads)}. "
            f"Payloads: {mock_emb.recorded_doc_calls[:2]}"
        )

        # CRITICAL CHECK 2: The tail evidence (>2000 chars) must be preserved in the assertion payloads
        tail_payloads = [
            p
            for p in mock_emb.recorded_doc_calls
            if "KUYRUK_BOLUMU" in p or "fesih" in p.lower()
        ]
        assert (
            len(tail_payloads) == 1
        ), f"Expected tail assertion to preserve its specific evidence! Payloads: {mock_emb.recorded_doc_calls}"

        # CRITICAL CHECK 3: Asymmetric role: verify document/passage role was used during projection
        assert len(mock_emb.recorded_doc_calls) == 8
        assert len(mock_emb.recorded_query_calls) == 0

        # Commit mutation so it is eligible for search
        async with engine.transaction() as db:
            await db.execute(
                "UPDATE memory_mutations SET state = 'COMMITTED' WHERE mutation_id = ?",
                (mutation["mutation_id"],),
            )
            await db.commit()

        # CRITICAL CHECK 4: Query role is used when searching
        search_results = await dao.search_v4_memory(
            tenant_id="tenant-test",
            agent_id="agent-p2",
            dataset_ids=["dataset-test"],
            query="Madde 8 fesih hükümleri",
            limit=5,
        )
        assert (
            len(mock_emb.recorded_query_calls) >= 1
        ), "Expected search to invoke query embedding role"

        # CRITICAL CHECK 5: Search results preserve raw vector score / distance
        assert len(search_results) > 0
        hit = search_results[0]
        assert "raw_scores" in hit or "retrieval_provenance" in hit

        # CRITICAL CHECK 6: Reindex / projection idempotency
        # Reprojection must replace stale representation metadata without
        # creating a duplicate physical vector.
        async with engine.transaction() as db:
            await db.execute(
                "UPDATE artifact_registry SET metadata_json = ? "
                "WHERE store_name = 'VECTOR' AND artifact_kind = 'ASSERTION_VECTOR' "
                "AND physical_artifact_id = ?",
                (
                    '{"representation_version":"legacy-v0"}',
                    assertions[0]["assertion_id"],
                ),
            )
            await db.execute(
                "UPDATE memory_artifacts SET metadata_json = ? "
                "WHERE store_name = 'VECTOR' AND artifact_kind = 'ASSERTION_VECTOR' "
                "AND artifact_id = ?",
                (
                    '{"representation_version":"legacy-v0"}',
                    assertions[0]["assertion_id"],
                ),
            )
            await db.commit()
        await dao.project_v4_vector_assertion(
            mutation=mutation, assertion=assertions[0]
        )
        count = await vec.count_records(active_only=True)
        assert (
            count.get("mesa_vectors_4", 0) == 8
        ), f"Expected exactly 8 vector records, got {count}"
        async with engine.connection() as db:
            cursor = await db.execute(
                "SELECT metadata_json FROM artifact_registry "
                "WHERE store_name = 'VECTOR' AND artifact_kind = 'ASSERTION_VECTOR' "
                "AND physical_artifact_id = ?",
                (assertions[0]["assertion_id"],),
            )
            metadata = (await cursor.fetchone())[0]
        assert V4_VECTOR_REPRESENTATION_VERSION in str(metadata)
    finally:
        await vec.close()


@pytest.mark.asyncio
async def test_phase2_2048_dimension_fence(tmp_path):
    """Verify that dimension mismatches fail closed as required by Phase 2."""
    db_path = str(tmp_path / "mesa_test_p2_dim.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)
    vec_path = tmp_path / "vec_p2_dim"
    vec = VectorEngine(uri=str(vec_path))
    await vec.initialize()

    from mesa_memory.embedding.service import EmbeddingIdentity

    mock_emb = MockEmbeddingService(dimension=768)  # wrong dimension!
    mock_emb._identity = EmbeddingIdentity(
        provider="nemotron",
        model="nvidia/nemotron-3-embed-1b",
        version="nemotron-qpass-v1",
        dimension=768,  # mismatch with 2048
        normalized=True,
        model_revision="nemotron-rev-1",
    )
    vec._embedding_service = mock_emb

    graph = SimpleNamespace(
        insert_node=AsyncMock(),
        insert_assertion=AsyncMock(),
        link_assertions=AsyncMock(),
        search=AsyncMock(return_value=[]),
        get_existing_node_ids=AsyncMock(return_value=set()),
    )
    dao = MemoryDAO(engine, vec, graph)

    try:
        candidate = MemoryCandidate.from_raw_log(
            raw_log_id=201,
            tenant_id="tenant-test",
            workspace_id="ws-test",
            dataset_id="dataset-test",
            document_id="doc-dim",
            revision_id="rev-1",
            chunk_id="chunk-dim",
            source_ref="ref-1",
            agent_id="agent-dim",
            session_id="session-dim",
            content_payload="Dimension test content",
            embedding_provider="nemotron",
            embedding_model="nvidia/nemotron-3-embed-1b",
            embedding_version="nemotron-qpass-v1",
            embedding_dimension=2048,  # Declared 2048
            embedding_space_id="nemotron:nvidia/nemotron-3-embed-1b:nemotron-qpass-v1:2048:norm=true",
            embedding_normalized=True,
            validation_mode=0,
        ).as_consolidation_record()

        await dao.record_mutation(candidate, raw_log_id=201)
        await dao.record_mutation_extraction(
            "agent-dim",
            candidate["mutation_id"],
            [
                {
                    "head": "TestHead",
                    "relation": "TestRel",
                    "tail": "TestTail",
                    "literal_value": None,
                }
            ],
        )
        await dao.set_mutation_state("agent-dim", candidate["mutation_id"], "VALIDATED")
        mutation = await dao.get_projection_mutation(str(candidate["mutation_id"]))
        assert mutation is not None

        await dao.project_v4_sql_entity(mutation=mutation, entity_name="TestHead")
        await dao.project_v4_sql_entity(mutation=mutation, entity_name="TestTail")
        await dao.project_v4_sql_assertion(
            mutation=mutation,
            triplet={
                "head": "TestHead",
                "relation": "TestRel",
                "tail": "TestTail",
                "literal_value": None,
            },
        )
        assertions = await dao.list_v4_assertions_for_mutation(
            str(mutation["mutation_id"])
        )

        with pytest.raises(ValueError, match="embedding dimension mismatch"):
            await dao.project_v4_vector_assertion(
                mutation=mutation, assertion=assertions[0]
            )
    finally:
        await vec.close()
