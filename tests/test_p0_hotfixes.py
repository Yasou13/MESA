import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from mesa_memory.storage import StorageFacade
from mesa_memory.security.rbac import AccessControl
from mesa_memory.schema.cmb import CMB, ResourceCost
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.adapter.base import BaseUniversalLLMAdapter


@pytest.fixture
def tmp_rbac_db(tmp_path):
    return str(tmp_path / "rbac.db")


@pytest.fixture
def storage_facade(tmp_path, tmp_rbac_db):
    ac = AccessControl(policy_path=tmp_rbac_db)
    return StorageFacade(
        raw_log_path=str(tmp_path / "raw_log.db"),
        vector_uri=str(tmp_path / "vector.lance"),
        graph_db_path=str(tmp_path / "graph.db"),
        graph_rocks_path=str(tmp_path / "graph.rocks"),
        access_control=ac,
    )


@pytest.mark.asyncio
async def test_rbac_system_spoof_blocked(storage_facade):
    """Test RBAC Spoofing Prevention.
    Attempt to execute a privileged operation using legacy string 'system'.
    """
    await storage_facade.initialize_all()

    cmb = CMB(
        cmb_id="test_rbac_001",
        content_payload="test payload",
        source="test",
        performative="test",
        resource_cost=ResourceCost(token_count=10, latency_ms=1.5),
    )

    with pytest.raises(PermissionError):
        await storage_facade.persist_cmb(
            cmb, agent_id="system", session_id="__mesa_system__"
        )


@pytest.mark.asyncio
async def test_graph_concurrent_read_write(storage_facade):
    """Test Graph Concurrency & Read Locks.
    Use asyncio.gather to launch multiple simultaneous graph read operations
    ALONGSIDE a graph write operation.
    """
    from mesa_memory.security.rbac_constants import SYSTEM_AGENT_ID, SYSTEM_SESSION_ID

    await storage_facade.initialize_all()

    # Setup initial graph data
    await storage_facade.graph.upsert_node(
        "NodeA", "ENTITY", "cmb1", SYSTEM_AGENT_ID, SYSTEM_SESSION_ID
    )
    await storage_facade.graph.upsert_node(
        "NodeB", "ENTITY", "cmb1", SYSTEM_AGENT_ID, SYSTEM_SESSION_ID
    )
    await storage_facade.graph.create_edge(
        "NodeA", "NodeB", "RELATES_TO", 1.0, SYSTEM_AGENT_ID, SYSTEM_SESSION_ID
    )

    # Launch concurrent reads and writes
    read_task1 = storage_facade.graph.get_node_by_id("NodeA")
    read_task2 = storage_facade.graph.get_neighbors("NodeA")
    write_task = storage_facade.graph.soft_delete_by_cmb("cmb1")

    # Assert they complete without raising RuntimeError or NetworkXError
    results = await asyncio.gather(
        read_task1, read_task2, write_task, return_exceptions=True
    )

    for r in results:
        assert not isinstance(r, Exception), f"Concurrent operation failed: {r}"


@pytest.mark.asyncio
async def test_consolidation_batch_embedding_calls(storage_facade):
    """Test N+1 Embedding Batching.
    Mock the embedder.aembed_batch method.
    Pass a mock batch of at least 3 records to ConsolidationLoop.run_batch().
    Assert that the mocked embedder was called exactly ONCE.
    """
    await storage_facade.initialize_all()

    embedder = AsyncMock(spec=BaseUniversalLLMAdapter)
    embedder.aembed_batch = AsyncMock(return_value=[[0.1] * 768 for _ in range(6)])

    llm_a = AsyncMock(spec=BaseUniversalLLMAdapter)
    llm_b = AsyncMock(spec=BaseUniversalLLMAdapter)
    obs_layer = MagicMock()

    loop = ConsolidationLoop(storage_facade, embedder, llm_a, llm_b, obs_layer)

    # Create 3 records
    batch = [
        {"cmb_id": "rec1", "content_payload": "test 1", "source": "s"},
        {"cmb_id": "rec2", "content_payload": "test 2", "source": "s"},
        {"cmb_id": "rec3", "content_payload": "test 3", "source": "s"},
    ]

    with patch.object(
        loop.rebel_extractor,
        "extract_triplets",
        return_value=[{"head": "H", "relation": "R", "tail": "T"}],
    ):
        await loop.run_batch(batch)

    embedder.aembed_batch.assert_called_once()


@pytest.mark.asyncio
async def test_tier3_worker_state_cleared(storage_facade):
    """Test Tier-3 Worker State Mutation.
    Insert a mock record into RawLogStorage with tier3_deferred = 1.
    Call clear_tier3_deferred.
    Assert tier3_deferred == 0.
    """
    await storage_facade.raw_log.initialize()

    cmb = CMB(
        cmb_id="tier3_001",
        content_payload="tier 3 payload",
        source="test",
        performative="test",
        tier3_deferred=True,
        resource_cost=ResourceCost(token_count=10, latency_ms=1.5),
    )

    await storage_facade.raw_log.insert_cmb(cmb)

    retrieved_initial = await storage_facade.raw_log.get_cmb("tier3_001")
    assert retrieved_initial["tier3_deferred"] == 1

    await storage_facade.raw_log.clear_tier3_deferred("tier3_001")

    retrieved_final = await storage_facade.raw_log.get_cmb("tier3_001")
    assert retrieved_final["tier3_deferred"] == 0
