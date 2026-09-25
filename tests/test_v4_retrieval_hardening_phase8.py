"""Phase 8 regression tests: Extraction / Graph Value Typing hardening.

Verifies:
1. Real entity object creates graph entity node and links subject to object.
2. Literal object is stored as literal_value without creating a second entity node.
3. Legal reference object classification.
4. Article reference object classification.
5. Long descriptive phrases or sentences passed in tail do NOT create auto-entity nodes; they become LITERAL.
6. Deterministic legal metadata binding from mutation (jurisdiction, valid_from, valid_to, chunk_id, etc.).
7. Extraction projection idempotency on replay/retry.
8. Graph projection reflects typed values accurately.
9. Backward compatibility with untyped legacy triplets.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import MemoryDAO, classify_graph_object
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


def _make_dao_with_recording_graph(
    engine: AsyncEngine,
) -> tuple[MemoryDAO, SimpleNamespace]:
    inserted_nodes: list[tuple[str, str, str]] = []
    inserted_assertions: list[dict] = []

    graph = SimpleNamespace(
        insert_node=AsyncMock(
            side_effect=lambda eid, name, aid: inserted_nodes.append((eid, name, aid))
        ),
        insert_assertion=AsyncMock(
            side_effect=lambda **kwargs: inserted_assertions.append(kwargs)
        ),
        link_assertions=AsyncMock(),
        traverse_paths=AsyncMock(return_value=[]),
        inserted_nodes=inserted_nodes,
        inserted_assertions=inserted_assertions,
    )
    vector = SimpleNamespace(
        compute_embedding=AsyncMock(return_value=[1.0, 0.0]),
        compute_query_embedding=AsyncMock(return_value=[1.0, 0.0]),
        upsert=AsyncMock(),
        search=AsyncMock(return_value=[]),
    )
    return MemoryDAO(engine, vector, graph), graph


# 1. Unit Tests for classify_graph_object
def test_p8_classify_graph_object_unit():
    """Verify classification logic for all supported types and anti-patterns."""
    # 1. Real entity
    t, literal, kind = classify_graph_object(tail="Yargıtay", literal_value=None)
    assert t == "Yargıtay"
    assert literal is None
    assert kind == "ENTITY"

    # 2. Literal value
    t, literal, kind = classify_graph_object(tail=None, literal_value="15000 TL")
    assert t is None
    assert literal == "15000 TL"
    assert kind == "LITERAL"

    # 3. Explicit type override
    t, literal, kind = classify_graph_object(
        tail="Some text", literal_value=None, object_type="LITERAL"
    )
    assert t is None
    assert literal == "Some text"
    assert kind == "LITERAL"

    # 4. Long phrase anti-pattern: must NOT be an entity!
    long_phrase = "Muaccel bir borcun borçlusu, alacaklının ihtarıyla temerrüde düşer."
    t, literal, kind = classify_graph_object(tail=long_phrase, literal_value=None)
    assert t is None
    assert literal == long_phrase
    assert kind == "LITERAL"

    # 5. Article reference
    t, literal, kind = classify_graph_object(tail="TBK m.117", literal_value=None)
    assert t == "TBK m.117"
    assert literal is None
    assert kind == "ARTICLE_REFERENCE"

    # 6. Legal reference
    t, literal, kind = classify_graph_object(tail="TBK", literal_value=None)
    assert t == "TBK"
    assert literal is None
    assert kind == "LEGAL_REFERENCE"

    # 7. Date string
    t, literal, kind = classify_graph_object(tail="2024-01-01", literal_value=None)
    assert t is None
    assert literal == "2024-01-01"
    assert kind == "DATE"


# 2. Integration Tests with DAO projection
@pytest.mark.asyncio
async def test_p8_long_phrase_does_not_create_entity_node(tmp_path):
    """Verify that projecting a triplet where tail is a long sentence creates NO entity node for the phrase."""
    db_path = str(tmp_path / "p8_phrase.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao, mock_graph = _make_dao_with_recording_graph(engine)
    tenant_id = "tenant-p8"
    agent_id = "agent-p8"
    dataset_id = "dataset-p8"

    try:
        cand = MemoryCandidate.from_raw_log(
            raw_log_id=1,
            tenant_id=tenant_id,
            workspace_id="workspace-p8",
            dataset_id=dataset_id,
            document_id="doc-p8-phrase",
            revision_id="rev-p8-1",
            chunk_id="chunk-p8-1",
            source_ref="source-p8-1",
            agent_id=agent_id,
            session_id="session-p8",
            content_payload="TBK 117 temerrüt kuralı açıklaması.",
            embedding_provider="test",
            embedding_model="catalog-contract",
            embedding_version="v1",
            embedding_dimension=2,
            embedding_space_id="test:catalog-contract:v1:2:norm=true",
            embedding_normalized=True,
        ).as_consolidation_record()
        await dao.record_mutation(cand, raw_log_id=1)
        mut = await dao.get_projection_mutation(str(cand["mutation_id"]))
        assert mut is not None

        # Triplet with long phrase mistakenly placed in tail
        long_sentence = (
            "Muaccel bir borcun borçlusu, alacaklının ihtarıyla temerrüde düşer."
        )
        triplet = {
            "head": "BorçlarKanunu",
            "relation": "hükmü",
            "tail": long_sentence,
            "evidence_span": long_sentence,
        }
        assertion_id = await dao.project_v4_graph_triplet(mutation=mut, triplet=triplet)
        assert assertion_id

        # Verify SQL assertion: object_entity_id must be None, literal_value must contain the phrase
        assertions = await dao.list_v4_assertions_for_mutation(str(mut["mutation_id"]))
        assert len(assertions) == 1
        ass = assertions[0]
        assert ass["object_entity_id"] is None
        assert ass["literal_value"] == long_sentence
        assert ass["object_type"] == "LITERAL"
        assert ass["representation_version"] == "assertion-v1"

        # Verify Kùzu Graph: Only 1 node (head: 'BorçlarKanunu') was inserted, NOT the sentence!
        inserted_names = [name for _, name, _ in mock_graph.inserted_nodes]
        assert "BorçlarKanunu" in inserted_names
        assert long_sentence not in inserted_names
        # Graph assertion received literal_value as object_value
        assert len(mock_graph.inserted_assertions) == 1
        assert mock_graph.inserted_assertions[0]["object_value"] == long_sentence
        assert mock_graph.inserted_assertions[0]["object_id"] is None
        assert mock_graph.inserted_assertions[0]["object_type"] == "LITERAL"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_p8_real_entity_creates_graph_node_and_link(tmp_path):
    """Verify that a concise, real entity object creates a graph node and links properly."""
    db_path = str(tmp_path / "p8_real_entity.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao, mock_graph = _make_dao_with_recording_graph(engine)
    tenant_id = "tenant-p8"
    agent_id = "agent-p8"
    dataset_id = "dataset-p8"

    try:
        cand = MemoryCandidate.from_raw_log(
            raw_log_id=10,
            tenant_id=tenant_id,
            workspace_id="workspace-p8",
            dataset_id=dataset_id,
            document_id="doc-p8-ent",
            revision_id="rev-p8-2",
            chunk_id="chunk-p8-2",
            source_ref="source-p8-2",
            agent_id=agent_id,
            session_id="session-p8",
            content_payload="Yargıtay ve Anayasa Mahkemesi ilişkisi.",
            embedding_provider="test",
            embedding_model="catalog-contract",
            embedding_version="v1",
            embedding_dimension=2,
            embedding_space_id="test:catalog-contract:v1:2:norm=true",
            embedding_normalized=True,
        ).as_consolidation_record()
        await dao.record_mutation(cand, raw_log_id=10)
        mut = await dao.get_projection_mutation(str(cand["mutation_id"]))
        assert mut is not None

        triplet = {
            "head": "Yargıtay",
            "relation": "başvurur",
            "tail": "AnayasaMahkemesi",
            "evidence_span": "Yargıtay norm denetimi için Anayasa Mahkemesine başvurur.",
        }
        assertion_id = await dao.project_v4_graph_triplet(mutation=mut, triplet=triplet)
        assert assertion_id

        assertions = await dao.list_v4_assertions_for_mutation(str(mut["mutation_id"]))
        assert len(assertions) == 1
        ass = assertions[0]
        # Real entity creates object_entity_id
        assert ass["object_entity_id"] is not None
        assert ass["literal_value"] is None
        assert ass["object_type"] == "ENTITY"

        # Both head and tail nodes are inserted into graph
        inserted_names = [name for _, name, _ in mock_graph.inserted_nodes]
        assert "Yargıtay" in inserted_names
        assert "AnayasaMahkemesi" in inserted_names
        assert mock_graph.inserted_assertions[0]["object_id"] is not None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_p8_deterministic_metadata_binding_from_mutation(tmp_path):
    """Verify that legal metadata (jurisdiction, authority_level, valid_from, valid_to, chunk_id) binds deterministically."""
    db_path = str(tmp_path / "p8_meta.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao, _ = _make_dao_with_recording_graph(engine)
    tenant_id = "tenant-p8"
    agent_id = "agent-p8"
    dataset_id = "dataset-p8"

    try:
        cand = MemoryCandidate.from_raw_log(
            raw_log_id=20,
            tenant_id=tenant_id,
            workspace_id="workspace-p8",
            dataset_id=dataset_id,
            document_id="doc-p8-meta",
            revision_id="rev-p8-3",
            chunk_id="chunk-p8-meta-3",
            source_ref="source-p8-meta-3",
            agent_id=agent_id,
            session_id="session-p8",
            content_payload="Mevzuat hükmü bağlama.",
            embedding_provider="test",
            embedding_model="catalog-contract",
            embedding_version="v1",
            embedding_dimension=2,
            embedding_space_id="test:catalog-contract:v1:2:norm=true",
            embedding_normalized=True,
            metadata={
                "jurisdiction": "TR",
                "authority_level": "PRIMARY",
                "valid_from": "2023-01-01",
                "valid_to": "2025-12-31",
            },
        ).as_consolidation_record()
        await dao.record_mutation(cand, raw_log_id=20)
        mut = await dao.get_projection_mutation(str(cand["mutation_id"]))
        assert mut is not None

        # Triplet does NOT specify jurisdiction or valid dates; must bind from mutation
        triplet = {
            "head": "CezaKanunu",
            "relation": "yaptırım",
            "literal_value": "Hapis cezası",
            "evidence_span": "Kişiye hapis cezası verilir.",
        }
        assertion_id = await dao.project_v4_sql_assertion(mutation=mut, triplet=triplet)
        assert assertion_id

        assertions = await dao.list_v4_assertions_for_mutation(str(mut["mutation_id"]))
        assert len(assertions) == 1
        ass = assertions[0]
        # Metadata bound deterministically from mutation!
        assert ass["jurisdiction"] == "TR"
        assert ass["authority_level"] == "PRIMARY"
        assert ass["valid_from"] == "2023-01-01"
        assert ass["valid_to"] == "2025-12-31"
        assert ass["chunk_id"] is not None
        assert ass["source_ref"] == "source-p8-meta-3"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_p8_extraction_projection_idempotency(tmp_path):
    """Verify that replaying or retrying the same triplet projection is idempotent and safe."""
    db_path = str(tmp_path / "p8_idempotent.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao, mock_graph = _make_dao_with_recording_graph(engine)
    tenant_id = "tenant-p8"
    agent_id = "agent-p8"
    dataset_id = "dataset-p8"

    try:
        cand = MemoryCandidate.from_raw_log(
            raw_log_id=30,
            tenant_id=tenant_id,
            workspace_id="workspace-p8",
            dataset_id=dataset_id,
            document_id="doc-p8-idem",
            revision_id="rev-p8-4",
            chunk_id="chunk-p8-4",
            source_ref="source-p8-4",
            agent_id=agent_id,
            session_id="session-p8",
            content_payload="İdempotency testi.",
            embedding_provider="test",
            embedding_model="catalog-contract",
            embedding_version="v1",
            embedding_dimension=2,
            embedding_space_id="test:catalog-contract:v1:2:norm=true",
            embedding_normalized=True,
        ).as_consolidation_record()
        await dao.record_mutation(cand, raw_log_id=30)
        mut = await dao.get_projection_mutation(str(cand["mutation_id"]))
        assert mut is not None

        triplet = {
            "head": "IdemHead",
            "relation": "idem_rel",
            "literal_value": "idem_val",
            "evidence_span": "idem evidence",
        }

        # First projection
        aid1 = await dao.project_v4_sql_assertion(mutation=mut, triplet=triplet)
        # Second projection (idempotent replay)
        aid2 = await dao.project_v4_sql_assertion(mutation=mut, triplet=triplet)

        assert aid1 == aid2
        assertions = await dao.list_v4_assertions_for_mutation(str(mut["mutation_id"]))
        # No duplicate assertion created in SQL
        assert len(assertions) == 1
    finally:
        await engine.close()
