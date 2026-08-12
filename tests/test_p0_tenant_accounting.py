import pytest

from mesa_memory.config import QueueAdmissionPolicy
from mesa_storage.dao import MemoryDAO, QueueOverCapacityError
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_tenant_workspace_dataset_boundary_isolation(tmp_path):
    """Verify catalog scoping prevents dataset or document cross-tenant and cross-workspace boundary leaks."""
    db_path = tmp_path / "mesa_test_tenant_accounting.db"
    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)

    dao = MemoryDAO(sqlite_engine=engine, vector_engine=None, graph_provider=None)

    # 1. Register Dataset A under Tenant A / Workspace A
    await dao.create_v4_workspace(
        tenant_id="tenant_A", workspace_id="ws_A", workspace_name="WS A"
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant_A", workspace_id="ws_A", dataset_id="dataset_shared"
    )

    # 2. Attempting to register dataset_shared under Tenant B / Workspace B MUST fail closed
    await dao.create_v4_workspace(
        tenant_id="tenant_B", workspace_id="ws_B", workspace_name="WS B"
    )
    with pytest.raises(
        ValueError,
        match="dataset identity collides|already bound|belongs to|cross tenant",
    ):
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant_B", workspace_id="ws_B", dataset_id="dataset_shared"
        )

    # 3. Attempting to create document for Tenant B under dataset_shared (which belongs to Tenant A) MUST fail closed
    with pytest.raises(ValueError, match="dataset does not belong to tenant"):
        await dao.create_v4_document(
            tenant_id="tenant_B",
            dataset_id="dataset_shared",
            document_id="doc_stolen",
            title="Stolen Document",
        )

    await engine.close()


@pytest.mark.asyncio
async def test_queue_quota_aggregates_two_agents_under_real_tenant(tmp_path):
    engine = AsyncEngine(str(tmp_path / "tenant-queue.db"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, vector_engine=None)
    policy = QueueAdmissionPolicy(
        queue_max_pending_records=10,
        queue_max_pending_bytes=10000,
        queue_max_pending_records_per_tenant=2,
        queue_max_pending_bytes_per_tenant=5000,
        queue_max_in_flight_records=10,
        queue_max_in_flight_records_per_tenant=2,
        queue_max_retry_pending_records=10,
        queue_max_retry_pending_records_per_tenant=2,
        queue_max_single_record_bytes=1000,
        queue_retry_after_seconds=1,
    )
    try:
        for agent_id in ("agent-a", "agent-b"):
            await dao.admit_raw_log(
                agent_id,
                {
                    "tenant_id": "tenant-shared",
                    "agent_id": agent_id,
                    "content": agent_id,
                },
                tenant_id="tenant-shared",
                policy=policy,
            )
        with pytest.raises(QueueOverCapacityError) as rejected:
            await dao.admit_raw_log(
                "agent-c",
                {
                    "tenant_id": "tenant-shared",
                    "agent_id": "agent-c",
                    "content": "third",
                },
                tenant_id="tenant-shared",
                policy=policy,
            )
        assert rejected.value.scope == "tenant"
        async with engine.connection() as db:
            rows = await (
                await db.execute(
                    "SELECT DISTINCT tenant_id, agent_id FROM dispatch_queue ORDER BY agent_id"
                )
            ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("tenant-shared", "agent-a"),
            ("tenant-shared", "agent-b"),
        ]
    finally:
        await engine.close()
