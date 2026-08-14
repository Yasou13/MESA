"""Independent Terra regressions for bypasses missed by the initial delta tests."""

from types import SimpleNamespace

import pytest

from mesa_memory.consolidation.router import _BoundedRoutingStates
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_revision_waits_for_each_manifest_chunk_not_duplicate_children(tmp_path):
    engine = AsyncEngine(str(tmp_path / "aggregate.db"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    tenant, workspace, dataset, document, revision = "tenant", "default", "main", "doc-1", "rev-1"
    await dao.ensure_v4_catalog_scope(tenant_id=tenant, workspace_id=workspace, dataset_id=dataset)
    await dao.create_v4_document(tenant_id=tenant, dataset_id=dataset, document_id=document, title="Document")
    await dao.create_v4_revision(tenant_id=tenant, dataset_id=dataset, document_id=document, revision_id=revision, revision_number=1, content_hash="a" * 64)
    for ordinal in range(2):
        await dao.create_v4_source_chunk(tenant_id=tenant, dataset_id=dataset, document_id=document, revision_id=revision, chunk_id=f"chunk-{ordinal}", title="Document", content_payload=f"payload {ordinal}", source_ref="test", chunk_ordinal=ordinal)
    async with engine.transaction() as db:
        await db.execute("INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, workspace_id, dataset_id, session_id, agent_id, state) VALUES ('pipeline', ?, ?, ?, 'session', 'agent', 'RUNNING')", (tenant, workspace, dataset))
        for raw_log_id, mutation_id in enumerate(("a", "b"), start=1):
            await db.execute("INSERT INTO memory_mutations (mutation_id, candidate_id, raw_log_id, tenant_id, workspace_id, dataset_id, document_id, revision_id, chunk_id, source_ref, evidence_span, agent_id, session_id, content_payload, metadata_json, source, pipeline_run_id, extraction_version, embedding_provider, embedding_model, embedding_version, embedding_dimension, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'test', '', 'agent', 'session', 'payload', '{}', 'api', 'pipeline', 'v4', 'local', 'model', 'v1', 384, 'RECEIVED')", (mutation_id, f"candidate-{mutation_id}", raw_log_id, tenant, workspace, dataset, document, revision, "chunk-0"))
            assert await dao._transition_memory_mutation_in_tx(db, mutation_id, to_state="COMMITTED")
        await db.commit()
    async with engine.connection() as db:
        row = await (await db.execute("SELECT status FROM document_revisions WHERE revision_id = ?", (revision,))).fetchone()
    assert row[0] == "PENDING"
    await engine.close()


def test_adaptive_routing_state_has_lru_capacity_bound():
    states = _BoundedRoutingStates(max_entries=3, ttl_seconds=60)
    for index in range(4):
        states.get_or_create(f"agent-{index}", 0.85)
    assert len(states._entries) == 3
    assert "agent-0" not in states._entries
