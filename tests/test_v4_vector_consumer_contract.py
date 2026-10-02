"""Fresh SQLite/LanceDB proof of the V4 vector consumer contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_memory.embedding.service import EmbeddingIdentity, EmbeddingService
from mesa_storage.dao import MemoryDAO
from mesa_storage.representation import V4_VECTOR_REPRESENTATION_VERSION
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine


IDENTITY = EmbeddingIdentity(
    provider="test",
    model="v4-vector-consumer",
    version="v1",
    dimension=4,
    normalized=True,
)


def _concept_vector(text: str) -> list[float]:
    folded = text.casefold()
    if any(term in folded for term in ("away from headquarters", "telecommuting")):
        return [1.0, 0.0, 0.0, 0.0]
    if any(term in folded for term in ("encrypted", "cipher")):
        return [0.0, 1.0, 0.0, 0.0]
    if any(term in folded for term in ("retained", "archival")):
        return [0.0, 0.0, 1.0, 0.0]
    return [0.0, 0.0, 0.0, 1.0]


@dataclass
class _Fixture:
    sql: AsyncEngine
    vector: VectorEngine
    dao: MemoryDAO
    assertions: dict[str, dict]
    mutations: dict[str, dict]

    async def close(self) -> None:
        await self.vector.close()
        await self.sql.close()


async def _ingest_assertion(
    fixture: _Fixture,
    *,
    raw_log_id: int,
    tenant_id: str,
    agent_id: str,
    dataset_id: str,
    key: str,
    head: str,
    relation: str,
    tail: str,
    evidence: str,
) -> None:
    await fixture.dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id,
        workspace_id=f"workspace-{tenant_id}",
        dataset_id=dataset_id,
    )
    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=raw_log_id,
        tenant_id=tenant_id,
        workspace_id=f"workspace-{tenant_id}",
        dataset_id=dataset_id,
        document_id=f"document-{key}",
        revision_id=f"revision-{key}",
        chunk_id=f"chunk-{key}",
        source_ref=f"source-{key}",
        agent_id=agent_id,
        session_id=f"session-{tenant_id}",
        content_payload=evidence,
        embedding_provider=IDENTITY.provider,
        embedding_model=IDENTITY.model,
        embedding_version=IDENTITY.version,
        embedding_dimension=IDENTITY.dimension,
        embedding_space_id=IDENTITY.embedding_space_id,
        embedding_normalized=IDENTITY.normalized,
        validation_mode=0,
    ).as_consolidation_record()
    await fixture.dao.record_mutation(candidate, raw_log_id=raw_log_id)
    await fixture.dao.record_mutation_extraction(
        agent_id,
        candidate["mutation_id"],
        [
            {
                "head": head,
                "relation": relation,
                "tail": tail,
                "fact_text": evidence,
                "source_span": evidence,
            }
        ],
    )
    await fixture.dao.set_mutation_state(
        agent_id, candidate["mutation_id"], "VALIDATED"
    )
    mutation = await fixture.dao.get_projection_mutation(candidate["mutation_id"])
    assert mutation is not None
    await fixture.dao.project_v4_sql_entity(mutation=mutation, entity_name=head)
    await fixture.dao.project_v4_sql_entity(mutation=mutation, entity_name=tail)
    await fixture.dao.project_v4_sql_assertion(
        mutation=mutation,
        triplet={
            "head": head,
            "relation": relation,
            "tail": tail,
            "fact_text": evidence,
            "source_span": evidence,
        },
    )
    assertions = await fixture.dao.list_v4_assertions_for_mutation(
        mutation["mutation_id"]
    )
    assert len(assertions) == 1
    await fixture.dao.project_v4_vector_assertion(
        mutation=mutation, assertion=assertions[0]
    )
    async with fixture.sql.transaction() as db:
        await db.execute(
            "UPDATE memory_mutations SET state = 'COMMITTED' WHERE mutation_id = ?",
            (mutation["mutation_id"],),
        )
        await db.commit()
    fixture.assertions[key] = assertions[0]
    fixture.mutations[key] = mutation


async def _build_fixture(tmp_path) -> _Fixture:
    sql = AsyncEngine(str(tmp_path / "mesa.sqlite"))
    vector = VectorEngine(
        str(tmp_path / "vectors.lance"),
        max_workers=1,
        embedding_service=EmbeddingService(
            identity=IDENTITY,
            provider_fn=_concept_vector,
            query_provider_fn=_concept_vector,
        ),
    )
    graph = SimpleNamespace(
        is_operational=True,
        search_v4_graph=AsyncMock(return_value=[]),
        insert_node=AsyncMock(),
        insert_assertion=AsyncMock(),
        link_assertions=AsyncMock(),
    )
    await sql.initialize()
    await initialize_schema(sql)
    await vector.initialize()
    fixture = _Fixture(sql, vector, MemoryDAO(sql, vector, graph), {}, {})
    await _ingest_assertion(
        fixture,
        raw_log_id=1,
        tenant_id="tenant-a",
        agent_id="agent-a",
        dataset_id="dataset-a",
        key="remote",
        head="MobilityRule",
        relation="ALLOWS",
        tail="DistributedStaff",
        evidence="Personnel may operate away from headquarters.",
    )
    await _ingest_assertion(
        fixture,
        raw_log_id=2,
        tenant_id="tenant-a",
        agent_id="agent-a",
        dataset_id="dataset-a",
        key="cipher",
        head="SecurityRule",
        relation="REQUIRES",
        tail="EncryptionControl",
        evidence="Records stay encrypted under the cipher mandate.",
    )
    await _ingest_assertion(
        fixture,
        raw_log_id=3,
        tenant_id="tenant-a",
        agent_id="agent-a",
        dataset_id="dataset-a",
        key="retention",
        head="RetentionRule",
        relation="REQUIRES",
        tail="ArchiveControl",
        evidence="Audit material is retained for archival review.",
    )
    return fixture


def _graph_hit(assertion: dict) -> dict:
    return {
        "entity_id": assertion["object_entity_id"],
        "paths": [
            {
                "assertion_ids": [assertion["assertion_id"]],
                "entity_ids": [
                    assertion["subject_id"],
                    assertion["object_entity_id"],
                ],
                "score": 1.0,
            }
        ],
    }


@pytest.mark.asyncio
async def test_real_lancedb_vector_lane_activation_rrf_and_provenance(tmp_path) -> None:
    fixture = await _build_fixture(tmp_path)
    try:
        cipher = fixture.assertions["cipher"]
        fixture.dao._graph.search_v4_graph.return_value = [_graph_hit(cipher)]
        diagnostics: dict = {}
        searches_before = fixture.vector.metrics.searches
        exact = await fixture.dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="cipher mandate",
            limit=10,
            certification_metadata=diagnostics,
        )
        hit = next(
            item for item in exact if item["assertion_id"] == cipher["assertion_id"]
        )
        provenance = hit["retrieval_provenance"]
        assert fixture.vector.metrics.searches == searches_before + 1
        assert set(provenance["origins"]) == {"vector", "bm25", "assertion", "graph"}
        assert set(provenance["lane_ranks"]) == {"vector", "bm25", "assertion", "graph"}
        assert provenance["vector"]["distance"] == provenance["raw_scores"]["vector"]
        assert provenance["vector"]["rank"] == provenance["lane_ranks"]["vector"]
        assert provenance["vector"]["registry_id"]
        assert provenance["vector"]["vector_id"] == cipher["assertion_id"]
        assert (
            provenance["vector"]["representation_version"]
            == V4_VECTOR_REPRESENTATION_VERSION
        )
        assert provenance["vector"]["embedding_space_id"] == IDENTITY.embedding_space_id
        assert provenance["vector"]["scope_identity"] == {
            "tenant_id": "tenant-a",
            "dataset_id": "dataset-a",
            "agent_id": "agent-a",
        }
        assert diagnostics["vector_lane"]["status"] == "active"
        assert diagnostics["vector_lane"]["search_executed"] is True
        assert diagnostics["vector_lane"]["candidate_count"] == 3
        assert diagnostics["vector_lane"]["allowed_vector_id_count"] == 3
        assert diagnostics["vector_lane"]["compatible_registry_artifact_count"] == 3

        fixture.dao._graph.search_v4_graph.return_value = []
        semantic = await fixture.dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="telecommuting arrangement",
            limit=3,
        )
        remote = next(
            item
            for item in semantic
            if item["assertion_id"] == fixture.assertions["remote"]["assertion_id"]
        )
        assert "vector" in remote["retrieval_provenance"]["origins"]
        assert "bm25" not in remote["retrieval_provenance"]["origins"]
    finally:
        await fixture.close()


@pytest.mark.asyncio
async def test_incompatible_and_missing_vectors_are_fail_closed_and_diagnosable(
    tmp_path,
) -> None:
    fixture = await _build_fixture(tmp_path)
    remote_id = fixture.assertions["remote"]["assertion_id"]
    try:
        async with fixture.sql.transaction() as db:
            cursor = await db.execute(
                "SELECT metadata_json FROM artifact_registry "
                "WHERE artifact_kind = 'ASSERTION_VECTOR' AND physical_artifact_id = ?",
                (remote_id,),
            )
            original_metadata = str((await cursor.fetchone())[0])
            await db.execute(
                "UPDATE artifact_registry SET metadata_json = "
                "json_set(metadata_json, '$.representation_version', 'legacy-v0') "
                "WHERE artifact_kind = 'ASSERTION_VECTOR' AND physical_artifact_id = ?",
                (remote_id,),
            )
            await db.commit()
        legacy_diagnostics: dict = {}
        legacy_results = await fixture.dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="telecommuting arrangement",
            certification_metadata=legacy_diagnostics,
        )
        assert all(
            "vector" not in item["retrieval_provenance"]["origins"]
            for item in legacy_results
            if item["assertion_id"] == remote_id
        )
        assert (
            legacy_diagnostics["vector_lane"]["rejection_reasons"][
                "representation_version"
            ]
            == 1
        )

        incompatible = json.loads(original_metadata)
        incompatible["dimension"] = 99
        incompatible["embedding_dimension"] = 99
        async with fixture.sql.transaction() as db:
            await db.execute(
                "UPDATE artifact_registry SET metadata_json = ? "
                "WHERE artifact_kind = 'ASSERTION_VECTOR' AND physical_artifact_id = ?",
                (json.dumps(incompatible), remote_id),
            )
            await db.commit()
        dimension_diagnostics: dict = {}
        await fixture.dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="telecommuting arrangement",
            certification_metadata=dimension_diagnostics,
        )
        assert dimension_diagnostics["vector_lane"]["rejection_reasons"] == {
            "dimension": 1,
            "embedding_dimension": 1,
        }

        async with fixture.sql.transaction() as db:
            await db.execute(
                "UPDATE artifact_registry SET metadata_json = ? "
                "WHERE artifact_kind = 'ASSERTION_VECTOR' AND physical_artifact_id = ?",
                (original_metadata, remote_id),
            )
            await db.commit()
        await fixture.vector.hard_delete(remote_id, "agent-a")
        missing_diagnostics: dict = {}
        await fixture.dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="telecommuting arrangement",
            certification_metadata=missing_diagnostics,
        )
        assert missing_diagnostics["vector_lane"]["missing_vector_id_count"] == 1
        assert missing_diagnostics["vector_lane"]["physical_vector_id_count"] == 2

        await fixture.vector.upsert(
            node_id="unregistered-vector",
            agent_id="agent-a",
            embedding=_concept_vector("telecommuting"),
        )
        results = await fixture.dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="telecommuting arrangement",
        )
        assert all(item["candidate_id"] != "unregistered-vector" for item in results)
    finally:
        await fixture.close()


@pytest.mark.asyncio
async def test_scope_tombstone_and_zero_compatible_contract_state(tmp_path) -> None:
    fixture = await _build_fixture(tmp_path)
    try:
        await _ingest_assertion(
            fixture,
            raw_log_id=10,
            tenant_id="tenant-b",
            agent_id="agent-b",
            dataset_id="dataset-b",
            key="other-scope",
            head="ExternalRule",
            relation="ALLOWS",
            tail="ExternalStaff",
            evidence="Personnel may operate away from headquarters.",
        )
        scoped = await fixture.dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="telecommuting arrangement",
        )
        assert all(
            item["assertion_id"] != fixture.assertions["other-scope"]["assertion_id"]
            for item in scoped
        )

        remote_id = fixture.assertions["remote"]["assertion_id"]
        async with fixture.sql.transaction() as db:
            await db.execute(
                "UPDATE v4_assertions SET status = 'TOMBSTONED' WHERE assertion_id = ?",
                (remote_id,),
            )
            await db.commit()
        tombstoned = await fixture.dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="telecommuting arrangement",
        )
        assert all(item["assertion_id"] != remote_id for item in tombstoned)

        async with fixture.sql.transaction() as db:
            await db.execute(
                "UPDATE artifact_registry SET metadata_json = "
                "json_set(metadata_json, '$.representation_version', 'legacy-v0') "
                "WHERE artifact_kind = 'ASSERTION_VECTOR'"
            )
            await db.commit()
        status = await fixture.dao.vector_consumer_status()
        assert status["status"] == "degraded"
        assert status["active_registry_artifacts"] == 4
        assert status["compatible_registry_artifacts"] == 0
        diagnostics: dict = {}
        await fixture.dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="cipher mandate",
            certification_metadata=diagnostics,
        )
        assert diagnostics["vector_lane"]["status"] == "no_compatible_artifacts"
        assert diagnostics["vector_lane"]["search_executed"] is False
    finally:
        await fixture.close()
