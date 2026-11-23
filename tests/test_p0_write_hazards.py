"""Verify canonical V4 SQLite intent is durably committed before secondary vector writes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.config import QueueAdmissionPolicy
from mesa_memory.embedding.service import EmbeddingIdentity
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_workers.projection_worker import process_projection_outbox_once


@pytest.mark.asyncio
async def test_canonical_sql_intent_established_before_secondary_writes(tmp_path):
    """Verify V4 canonical SQLite rows exist before VectorEngine.upsert is invoked."""
    db_path = tmp_path / "mesa_test_hazards.db"
    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)

    identity = EmbeddingIdentity(
        provider="test", model="hazard", version="v1", dimension=3, normalized=True
    )
    sql_seen_at_upsert: list[int] = []

    async def _recording_upsert(**_kwargs):
        async with engine.connection() as db:
            async with db.execute("SELECT COUNT(*) FROM v4_assertions") as cursor:
                row = await cursor.fetchone()
                sql_seen_at_upsert.append(int(row[0]))

    mock_vec = SimpleNamespace(
        is_initialized=True,
        semantic_operational=True,
        embedding_identity=identity,
        compute_embedding=AsyncMock(return_value=[0.1, 0.2, 0.3]),
        upsert=AsyncMock(side_effect=_recording_upsert),
        search=AsyncMock(return_value=[]),
    )

    dao = MemoryDAO(
        sqlite_engine=engine,
        vector_engine=mock_vec,
        embedding_enabled=True,
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant-hazard", workspace_id="ws-h", dataset_id="ds-h"
    )
    sess = await dao.create_v4_session(
        tenant_id="tenant-hazard",
        workspace_id="ws-h",
        dataset_ids=["ds-h"],
        agent_id="agent_hazard_test",
        principal_id="p-h",
    )
    admission = await dao.admit_v4_memory(
        tenant_id="tenant-hazard",
        workspace_id="ws-h",
        dataset_id="ds-h",
        agent_id="agent_hazard_test",
        session_id=sess["session_id"],
        document_id="doc-hazard",
        revision_id="rev-hazard-1",
        chunk_id="chunk-hazard-0",
        title="HazardEntity",
        content_payload="Test content for canonical write hazard",
        source_ref="file://hazard.txt",
        evidence_span="Test content for canonical write hazard",
        revision_number=1,
        chunk_ordinal=0,
        supersedes_revision_id=None,
        metadata={},
        embedding_provider=identity.provider,
        embedding_model=identity.model,
        embedding_version=identity.version,
        embedding_dimension=identity.dimension,
        embedding_space_id=identity.embedding_space_id,
        embedding_normalized=True,
        policy=QueueAdmissionPolicy(),
        validation_mode=0,
    )
    mutation_id = admission["response"]["mutation_id"]
    await dao.record_mutation_extraction("agent_hazard_test", mutation_id, [])
    await dao.set_mutation_state("agent_hazard_test", mutation_id, "VALIDATED")

    # SQL lane runs first; vector upsert has NOT been called yet.
    assert mock_vec.upsert.await_count == 0
    await process_projection_outbox_once(dao, worker_id="hazard-w", limit=1)
    assert mock_vec.upsert.await_count == 0

    # Vector lane runs next; canonical v4_assertions row is already committed in SQLite.
    await process_projection_outbox_once(dao, worker_id="hazard-w", limit=1)
    assert mock_vec.upsert.await_count == 1
    assert sql_seen_at_upsert == [1]

    await engine.close()
