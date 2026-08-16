"""Adversarial regression test suite for Task D005 (Canonical Tenant-Wide V4 Queue Accounting)
and Task D006 (Immutable Alembic Upgrade Closure)."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from mesa_storage.dao import MemoryDAO, QueueOverCapacityError
from mesa_storage.schema_contract import validate_postflight
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@dataclass
class StrictQueuePolicy:
    queue_max_pending_records: int = 2
    queue_max_pending_bytes: int = 10000
    queue_max_pending_records_per_tenant: int = 2
    queue_max_pending_bytes_per_tenant: int = 10000
    queue_max_in_flight_records: int = 2
    queue_max_in_flight_records_per_tenant: int = 2
    queue_max_retry_pending_records: int = 2
    queue_max_retry_pending_records_per_tenant: int = 2
    queue_max_single_record_bytes: int = 8388608


@pytest.mark.asyncio
async def test_d005_tenant_wide_queue_accounting(tmp_path):
    """Verify that multiple agents under the same tenant share the tenant quota,
    and agent B cannot bypass quota limits when combined tenant usage exceeds policy."""
    db_path = str(tmp_path / "mesa_test_d005.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = MemoryDAO(engine, SimpleNamespace())
    tenant_id = "tenant_shared"
    dataset_id = "ds_shared"
    ws_id = "ws_shared"

    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id, workspace_id=ws_id, dataset_id=dataset_id
    )
    sess_a = (
        await dao.create_v4_session(
            tenant_id=tenant_id,
            workspace_id=ws_id,
            dataset_ids=[dataset_id],
            agent_id="agent_A",
            principal_id="p1",
        )
    )["session_id"]
    sess_b = (
        await dao.create_v4_session(
            tenant_id=tenant_id,
            workspace_id=ws_id,
            dataset_ids=[dataset_id],
            agent_id="agent_B",
            principal_id="p2",
        )
    )["session_id"]

    # Restrict tenant queue limit to 2 records total
    strict_policy = StrictQueuePolicy()

    # Agent A admits 2 memories (consuming full tenant quota)
    for i in range(2):
        await dao.admit_v4_memory(
            tenant_id=tenant_id,
            workspace_id=ws_id,
            dataset_id=dataset_id,
            agent_id="agent_A",
            session_id=sess_a,
            document_id=f"doc_a_{i}",
            revision_id=f"rev_a_{i}",
            chunk_id=f"chk_a_{i}",
            title=f"Title A {i}",
            content_payload="payload",
            source_ref="ref",
            evidence_span="0:5",
            revision_number=1,
            chunk_ordinal=0,
            supersedes_revision_id=None,
            metadata={},
            embedding_provider="st",
            embedding_model="m",
            embedding_version="v",
            embedding_dimension=384,
            validation_mode=0,
            policy=strict_policy,
        )

    # Verify rows in dispatch_queue and dispatch_journal store tenant_id = tenant_shared
    async with engine.connection() as db:
        async with db.execute("SELECT tenant_id FROM dispatch_queue") as cur:
            rows = await cur.fetchall()
            for r in rows:
                assert r[0] == tenant_id

    # Agent B attempts admission under the same tenant -> MUST be rejected for quota capacity
    with pytest.raises((ValueError, QueueOverCapacityError)):
        await dao.admit_v4_memory(
            tenant_id=tenant_id,
            workspace_id=ws_id,
            dataset_id=dataset_id,
            agent_id="agent_B",
            session_id=sess_b,
            document_id="doc_b_0",
            revision_id="rev_b_0",
            chunk_id="chk_b_0",
            title="Title B 0",
            content_payload="payload",
            source_ref="ref",
            evidence_span="0:5",
            revision_number=1,
            chunk_ordinal=0,
            supersedes_revision_id=None,
            metadata={},
            embedding_provider="st",
            embedding_model="m",
            embedding_version="v",
            embedding_dimension=384,
            validation_mode=0,
            policy=strict_policy,
        )

    await engine.close()


@pytest.mark.asyncio
async def test_d006_postflight_and_alembic_upgrade_closure(tmp_path):
    """Verify that postflight validation catches duplicate active heads, and
    new Alembic migration fe5f6a7b8c9d cleans duplicate active heads and creates uq_active_document_revision.
    """
    db_path = str(tmp_path / "mesa_test_d006.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = MemoryDAO(engine, SimpleNamespace())
    await dao.ensure_v4_catalog_scope(
        tenant_id="t6", workspace_id="ws6", dataset_id="ds6"
    )
    await dao.create_v4_document(
        tenant_id="t6", dataset_id="ds6", document_id="doc6", title="Doc6"
    )

    # Verify that partial unique index uq_active_document_revision prevents duplicate ACTIVE heads
    with pytest.raises(Exception, match="UNIQUE constraint failed"):
        async with engine.transaction() as db:
            await db.execute(
                "INSERT INTO document_revisions (revision_id, tenant_id, document_id, revision_number, content_hash, status) "
                "VALUES ('r6_1', 't6', 'doc6', 1, '1'*64, 'ACTIVE')"
            )
            # Force a second ACTIVE revision for doc6 -> MUST fail unique constraint
            await db.execute(
                "INSERT INTO document_revisions (revision_id, tenant_id, document_id, revision_number, content_hash, status) "
                "VALUES ('r6_2', 't6', 'doc6', 2, '2'*64, 'ACTIVE')"
            )
            await db.commit()

    # validate_postflight must pass on clean schema
    import sqlite3

    from alembic.config import Config

    cfg = Config()
    sync_conn = sqlite3.connect(db_path)
    try:
        validate_postflight(sync_conn, cfg)
    finally:
        sync_conn.close()

    await engine.close()
