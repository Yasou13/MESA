"""Independent proof through extraction, durable outbox, SQL and real Kuzu."""

import json
from types import SimpleNamespace

import pytest

from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_memory.embedding.service import EmbeddingIdentity, EmbeddingService
from mesa_memory.extraction.service import (
    FactExtractionService,
    fact_candidates_to_extracted_triplet,
)
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema_artifact
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from mesa_workers.projection_worker import process_projection_outbox_once

_MATRIX = [
    ("30 gün", None, "LITERAL"),
    ("15 gün", None, "LITERAL"),
    ("3 kişi", None, "LITERAL"),
    ("15000 TL", None, "LITERAL"),
    ("15.000 TL", None, "LITERAL"),
    ("15.000,50 TL", None, "LITERAL"),
    ("%25", None, "LITERAL"),
    ("5 kilogram", None, "LITERAL"),
    ("10 kilometre", None, "LITERAL"),
    ("24 saat", None, "LITERAL"),
    ("6 ay", None, "LITERAL"),
    ("2026-09-26", None, "DATE"),
    ("26 Eylül 2026", None, "DATE"),
    ("2024 yılı", None, "DATE"),
    ("TBK 117", None, "LEGAL_REFERENCE"),
    ("TCK 86", None, "LEGAL_REFERENCE"),
    ("CMK 141", None, "LEGAL_REFERENCE"),
    ("117 TBK", None, "LEGAL_REFERENCE"),
    ("Türk Borçlar Kanunu", None, "LEGAL_REFERENCE"),
    ("madde 117", None, "ARTICLE_REFERENCE"),
    ("117. madde", None, "ARTICLE_REFERENCE"),
    ("Ahmet Yılmaz", None, "ENTITY"),
    ("Ankara", None, "ENTITY"),
    ("Yargıtay", None, "ENTITY"),
    ("OpenAI", None, "ENTITY"),
    ("Microsoft", None, "ENTITY"),
    # These values cannot be correctly recovered by downstream heuristics.
    ("kırmızı", "LITERAL", "LITERAL"),
    ("yarın", "DATE", "DATE"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("conversion", ["canonical", "additional_triplets"])
async def test_extraction_type_scope_and_source_survive_real_outbox(
    tmp_path, conversion
):
    sql = AsyncEngine(str(tmp_path / "audit.sqlite"))
    identity = EmbeddingIdentity(
        provider="test", model="audit", version="v1", dimension=2
    )
    vector = VectorEngine(
        str(tmp_path / "audit.lance"),
        max_workers=1,
        embedding_service=EmbeddingService(
            identity=identity, provider_fn=lambda text: [1.0, 0.0]
        ),
    )
    graph_path = tmp_path / "graph"
    await sql.initialize()
    await initialize_schema(sql)
    await vector.initialize()
    initialize_schema_artifact(str(graph_path))
    graph = KuzuGraphProvider(str(graph_path), max_workers=1)
    await graph.initialize()
    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vector, graph_provider=graph)
    facts_json = [
        {
            "fact_text": f"Subject value {value}",
            "subject": "Subject",
            "predicate": f"value_{i}",
            "object": value,
            "source_span": f"Subject value {value}",
            "confidence": 0.9,
            "valid_from": "2025-01-01",
            "valid_to": "2027-01-01",
            "metadata": {"jurisdiction": "DE" if i % 2 else "TR"},
            **({"object_type": explicit} if explicit else {}),
        }
        for i, (value, explicit, _) in enumerate(_MATRIX)
    ]
    source = "\n".join(f["fact_text"] for f in facts_json)
    service = FactExtractionService(
        SimpleNamespace(complete=lambda *a, **k: json.dumps({"facts": facts_json}))
    )
    try:
        facts = await service.extract_facts(source)
        assert len(facts) == len(_MATRIX)
        if conversion == "canonical":
            triplets = ConsolidationLoop._canonical_triplets(facts)
        else:
            extracted = fact_candidates_to_extracted_triplet(facts)
            assert extracted is not None
            triplets = [
                t.model_dump(exclude={"additional_triplets", "record_index"})
                for t in extracted.all_triplets()
            ]
        candidate = MemoryCandidate.from_raw_log(
            raw_log_id=1,
            tenant_id="tenant-a",
            agent_id="agent-a",
            session_id="session-a",
            workspace_id="ws-a",
            dataset_id="ds-a",
            document_id="doc-a",
            revision_id="rev-a",
            chunk_id="chunk-a",
            content_payload=source,
            embedding_provider=identity.provider,
            embedding_model=identity.model,
            embedding_version=identity.version,
            embedding_dimension=identity.dimension,
        ).as_consolidation_record()
        await dao.record_mutation(candidate, raw_log_id=1)
        assert await dao.record_mutation_extraction(
            "agent-a", candidate["mutation_id"], triplets
        )
        durable = await dao.get_projection_mutation(candidate["mutation_id"])
        assert [t.get("object_type") for t in durable["projection_triplets"]] == [
            kind for _, _, kind in _MATRIX
        ]
        await dao.set_mutation_state("agent-a", candidate["mutation_id"], "VALIDATED")
        for _ in range(3):
            result = await process_projection_outbox_once(
                dao, worker_id="audit-projector"
            )
            assert result == {
                "claimed": 1,
                "completed": 1,
                "retry_pending": 0,
                "dead_letter": 0,
            }
        assertions = await dao.list_v4_assertions_for_mutation(candidate["mutation_id"])
        assert len(assertions) == len(_MATRIX)
        by_predicate = {a["predicate"]: a for a in assertions}
        for i, (value, _, kind) in enumerate(_MATRIX):
            assertion = by_predicate[f"value_{i}"]
            assert assertion["object_type"] == kind
            assert (
                assertion["jurisdiction"] == facts_json[i]["metadata"]["jurisdiction"]
            )
            assert assertion["evidence_span"] == facts_json[i]["source_span"]
            assert assertion["valid_from"].startswith("2025-01-01")
            assert assertion["valid_to"].startswith("2027-01-01")
            assert (assertion["object_entity_id"] is not None) == (kind == "ENTITY")
            if kind != "ENTITY":
                assert assertion["literal_value"] == value
        graph_nodes = await graph.execute_query("MATCH (e:Entity) RETURN e.name", {})
        expected_entities = {
            "Subject",
            *(value for value, _, kind in _MATRIX if kind == "ENTITY"),
        }
        assert {row[0] for row in graph_nodes} == expected_entities
        async with sql.connection() as db:
            rows = await (
                await db.execute("SELECT canonical_name FROM v4_entities")
            ).fetchall()
        assert {row[0] for row in rows} == expected_entities
        graph_assertions = await graph.execute_query(
            "MATCH (a:Assertion) RETURN a.predicate, a.object_type, a.object_value", {}
        )
        assert len(graph_assertions) == len(_MATRIX)
        for pred, kind, value in graph_assertions:
            expected_value, _, expected_kind = _MATRIX[int(pred.split("_")[1])]
            assert kind == expected_kind
            if kind != "ENTITY":
                assert value == expected_value
    finally:
        await graph.close()
        await vector.close()
        await sql.close()
