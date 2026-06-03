"""
Phase 1.1 Verification: Split-Brain Elimination — Storage Unification Tests.

Proves that:
  1. The deleted ``mesa_memory.storage`` module is unreachable at import time.
  2. The cold-path ingestion worker routes ALL writes exclusively through
     ``MemoryDAO.insert_memory`` (Dual-Write Saga pattern B-7).
  3. No residual coupling exists between production code and the deleted
     StorageFacade / RawLogStorage / VectorStorage / NetworkXProvider.
  4. ``ConsolidationLoop`` reads/writes exclusively via MemoryDAO.
  5. ``HybridRetriever`` is wired to MemoryDAO (not StorageFacade).

asyncio_mode = strict → every async test requires explicit @pytest.mark.asyncio.
"""

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mesa_storage.dao import MemoryDAO

# ===================================================================
# Helpers
# ===================================================================


def _make_mock_dao() -> MagicMock:
    """Build a mock MemoryDAO with all async methods pre-configured."""
    dao = MagicMock(spec=MemoryDAO)
    dao.get_raw_log = AsyncMock(
        return_value={
            "id": 1,
            "payload": {
                "agent_id": "test_agent",
                "session_id": "test_session",
                "content": "EU regulation Article 5 mandates data protection compliance.",
                "metadata": {},
            },
            "status": "queued",
            "created_at": "2026-05-30T00:00:00Z",
        }
    )
    dao.update_raw_log_status = AsyncMock()
    dao.get_memories = AsyncMock(return_value=[])
    dao.insert_memory = AsyncMock(return_value="node-uuid-001")
    dao.insert_edge = AsyncMock(return_value="edge-uuid-001")
    dao.mark_consolidated = AsyncMock()
    dao.invalidate_node = AsyncMock()
    dao.find_nodes_by_name = AsyncMock(return_value=[])
    dao.get_node_degree = AsyncMock(return_value=0)
    dao.search_memory = AsyncMock(return_value=[])
    dao.search_memory_fts = AsyncMock(return_value=[])
    dao.get_all_edges = AsyncMock(return_value=[])
    dao.insert_raw_log = AsyncMock(return_value=1)
    dao.health_check = AsyncMock(return_value={"sqlite": "ok", "vector": "ok"})
    return dao


# ===================================================================
# TEST 1: Old storage module is unreachable
# ===================================================================


class TestStorageFacadeDeleted:
    """Verify the legacy mesa_memory.storage module is physically gone."""

    def test_import_mesa_memory_storage_raises_import_error(self):
        """Importing mesa_memory.storage must raise ImportError or
        ModuleNotFoundError — the directory has been deleted."""
        # Ensure any stale __pycache__ doesn't mask the deletion
        for key in list(sys.modules.keys()):
            if key.startswith("mesa_memory.storage"):
                del sys.modules[key]

        with pytest.raises((ImportError, ModuleNotFoundError)):
            importlib.import_module("mesa_memory.storage")

    def test_import_storage_facade_raises_import_error(self):
        """StorageFacade class is unreachable."""
        for key in list(sys.modules.keys()):
            if key.startswith("mesa_memory.storage"):
                del sys.modules[key]

        with pytest.raises((ImportError, ModuleNotFoundError)):
            from mesa_memory.storage import StorageFacade  # noqa: F401

    def test_import_raw_log_storage_raises_import_error(self):
        """RawLogStorage is unreachable."""
        for key in list(sys.modules.keys()):
            if key.startswith("mesa_memory.storage"):
                del sys.modules[key]

        with pytest.raises((ImportError, ModuleNotFoundError)):
            from mesa_memory.storage.raw_log import RawLogStorage  # noqa: F401

    def test_import_vector_storage_raises_import_error(self):
        """VectorStorage is unreachable."""
        for key in list(sys.modules.keys()):
            if key.startswith("mesa_memory.storage"):
                del sys.modules[key]

        with pytest.raises((ImportError, ModuleNotFoundError)):
            from mesa_memory.storage.vector_index import VectorStorage  # noqa: F401

    def test_import_networkx_provider_raises_import_error(self):
        """NetworkXProvider is unreachable."""
        for key in list(sys.modules.keys()):
            if key.startswith("mesa_memory.storage"):
                del sys.modules[key]

        with pytest.raises((ImportError, ModuleNotFoundError)):
            from mesa_memory.storage.graph.networkx_provider import (
                NetworkXProvider,  # noqa: F401
            )


# ===================================================================
# TEST 2: No production module imports from mesa_memory.storage
# ===================================================================


class TestNoProductionImportsFromDeletedStorage:
    """Static analysis: verify no production module has a live import
    from the deleted ``mesa_memory.storage`` package."""

    PRODUCTION_MODULES = [
        "mesa_workers.ingestion_worker",
        "mesa_memory.consolidation.loop",
        "mesa_memory.consolidation.writer",
        "mesa_memory.consolidation.router",
        "mesa_memory.consolidation.validator",
        "mesa_memory.valence.novelty",
        "mesa_memory.valence.core",
        "mesa_memory.retrieval.hybrid",
        "mesa_memory.api.server",
        "mesa_storage.dao",
    ]

    @pytest.mark.parametrize("module_name", PRODUCTION_MODULES)
    def test_no_storage_facade_import(self, module_name: str):
        """Module must not import from mesa_memory.storage."""
        import inspect

        mod = importlib.import_module(module_name)
        source = inspect.getsource(mod)

        # Must not contain a live import of StorageFacade
        assert "from mesa_memory.storage import" not in source, (
            f"{module_name} still imports from the deleted "
            f"mesa_memory.storage package"
        )
        assert "import mesa_memory.storage" not in source, (
            f"{module_name} still imports the deleted " f"mesa_memory.storage package"
        )


# ===================================================================
# TEST 3: Cold-path ingestion routes exclusively through MemoryDAO
# ===================================================================


class TestColdPathRoutesToDAO:
    """Prove that process_cold_path calls MemoryDAO.insert_memory
    exactly once (no triplets case) and never touches old storage."""

    @pytest.mark.asyncio
    async def test_cold_path_calls_dao_insert_memory_on_no_triplets(self):
        """When REBEL extracts no triplets, the worker commits a raw
        memory node via dao.insert_memory — exactly once."""
        from mesa_workers.ingestion_worker import process_cold_path

        dao = _make_mock_dao()

        mock_adapter = MagicMock()
        mock_adapter.aembed = AsyncMock(return_value=[0.1] * 1536)
        mock_adapter.acomplete = AsyncMock(return_value="[]")

        # Patch REBEL to return no triplets (exercise raw-memory commit path)
        with (
            patch(
                "mesa_workers.ingestion_worker._get_rebel_extractor",
                return_value=None,
            ),
            patch(
                "mesa_workers.ingestion_worker._run_llm_triplet_extraction",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "mesa_workers.ingestion_worker.AdapterFactory.get_adapter",
                return_value=mock_adapter,
            ),
        ):
            await process_cold_path(log_id=1, agent_id="test_agent", dao=dao)

        # Assert: get_raw_log was called to retrieve the payload
        dao.get_raw_log.assert_awaited_once_with("test_agent", 1)

        # Assert: status was transitioned to processing
        dao.update_raw_log_status.assert_any_await("test_agent", 1, "processing")

        # Assert: insert_memory was called EXACTLY once (raw memory node)
        assert dao.insert_memory.await_count == 1, (
            f"Expected exactly 1 insert_memory call, "
            f"got {dao.insert_memory.await_count}"
        )

        # Assert: status was finalized to processed
        dao.update_raw_log_status.assert_any_await("test_agent", 1, "processed")

    @pytest.mark.asyncio
    async def test_cold_path_calls_dao_insert_memory_and_edge_with_triplets(self):
        """When REBEL extracts triplets, the worker commits nodes + edges
        via dao.insert_memory and dao.insert_edge."""
        from mesa_workers.ingestion_worker import process_cold_path

        dao = _make_mock_dao()

        fake_triplets = [
            {"head": "EU", "relation": "mandates", "tail": "Data Protection"},
        ]

        mock_adapter = MagicMock()
        mock_adapter.aembed = AsyncMock(return_value=[0.1] * 1536)
        mock_adapter.acomplete = AsyncMock(return_value="[]")

        with (
            patch(
                "mesa_workers.ingestion_worker._get_rebel_extractor",
                return_value=None,
            ),
            patch(
                "mesa_workers.ingestion_worker._run_llm_triplet_extraction",
                new_callable=AsyncMock,
                return_value=fake_triplets,
            ),
            patch(
                "mesa_workers.ingestion_worker.AdapterFactory.get_adapter",
                return_value=mock_adapter,
            ),
        ):
            await process_cold_path(log_id=1, agent_id="test_agent", dao=dao)

        # Assert: insert_memory called for head + tail = 2 calls
        assert dao.insert_memory.await_count == 2, (
            f"Expected 2 insert_memory calls (head + tail), "
            f"got {dao.insert_memory.await_count}"
        )

        # Assert: insert_edge called exactly once for the triplet
        assert dao.insert_edge.await_count == 1, (
            f"Expected 1 insert_edge call, " f"got {dao.insert_edge.await_count}"
        )

        # Assert: finalized
        dao.update_raw_log_status.assert_any_await("test_agent", 1, "processed")

    @pytest.mark.asyncio
    async def test_cold_path_rejects_missing_fields(self):
        """When payload is missing agent_id, cold-path rejects via DAO status update."""
        from mesa_workers.ingestion_worker import process_cold_path

        dao = _make_mock_dao()
        dao.get_raw_log = AsyncMock(
            return_value={
                "id": 1,
                "payload": {
                    "agent_id": "",  # Empty — should trigger rejection
                    "content": "Some content",
                },
                "status": "queued",
                "created_at": "2026-05-30T00:00:00Z",
            }
        )

        await process_cold_path(log_id=1, agent_id="", dao=dao)

        # Assert: rejected status with error reason
        dao.update_raw_log_status.assert_awaited_once_with(
            "", 1, "rejected", error_reason="missing_agent_id_or_content"
        )

        # Assert: insert_memory was NEVER called (pipeline short-circuited)
        dao.insert_memory.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cold_path_skips_non_queued(self):
        """If raw_log status is not 'queued', the worker skips silently."""
        from mesa_workers.ingestion_worker import process_cold_path

        dao = _make_mock_dao()
        dao.get_raw_log = AsyncMock(
            return_value={
                "id": 1,
                "payload": {"agent_id": "a", "content": "c"},
                "status": "processed",  # Already done
                "created_at": "2026-05-30T00:00:00Z",
            }
        )

        await process_cold_path(log_id=1, agent_id="a", dao=dao)

        # Assert: no status update, no insert
        dao.update_raw_log_status.assert_not_awaited()
        dao.insert_memory.assert_not_awaited()


# ===================================================================
# TEST 4: ConsolidationLoop is wired to MemoryDAO
# ===================================================================


class TestConsolidationLoopDAOWiring:
    """Verify ConsolidationLoop reads from MemoryDAO, not StorageFacade."""

    def test_constructor_accepts_dao(self):
        """ConsolidationLoop.__init__ accepts a MemoryDAO instance."""
        from mesa_memory.consolidation.loop import ConsolidationLoop
        from mesa_memory.observability.metrics import ObservabilityLayer

        dao = _make_mock_dao()
        obs = ObservabilityLayer()
        embedder = MagicMock()
        llm_a = MagicMock()
        llm_b = MagicMock()

        loop = ConsolidationLoop(
            dao=dao,
            embedder=embedder,
            llm_a=llm_a,
            llm_b=llm_b,
            obs_layer=obs,
        )

        assert loop.dao is dao

    @pytest.mark.asyncio
    async def test_run_batch_reads_from_dao_when_no_batch_given(self):
        """When run_batch(batch=None), it reads from dao.get_memories."""
        from mesa_memory.consolidation.loop import ConsolidationLoop
        from mesa_memory.observability.metrics import ObservabilityLayer

        dao = _make_mock_dao()
        dao.get_memories = AsyncMock(return_value=[])  # Empty — no work
        obs = ObservabilityLayer()
        embedder = MagicMock()
        llm_a = MagicMock()
        llm_b = MagicMock()

        loop = ConsolidationLoop(
            dao=dao,
            embedder=embedder,
            llm_a=llm_a,
            llm_b=llm_b,
            obs_layer=obs,
        )

        await loop.run_batch(batch=None)

        # Assert: get_memories was called to fetch unconsolidated records
        dao.get_memories.assert_awaited()


# ===================================================================
# TEST 5: HybridRetriever is wired to MemoryDAO
# ===================================================================


class TestHybridRetrieverDAOWiring:
    """Verify HybridRetriever accepts MemoryDAO, not StorageFacade."""

    def test_constructor_accepts_dao(self):
        """HybridRetriever.__init__ accepts a MemoryDAO 'dao' parameter."""
        from mesa_memory.retrieval.hybrid import HybridRetriever

        dao = _make_mock_dao()
        analyzer = MagicMock()
        embedder = MagicMock()

        retriever = HybridRetriever(
            dao=dao,
            analyzer=analyzer,
            embedder=embedder,
        )

        assert retriever.dao is dao

    def test_constructor_has_no_storage_facade_parameter(self):
        """HybridRetriever must NOT accept a 'storage_facade' parameter."""
        import inspect

        from mesa_memory.retrieval.hybrid import HybridRetriever

        sig = inspect.signature(HybridRetriever.__init__)
        param_names = list(sig.parameters.keys())

        assert "storage_facade" not in param_names, (
            "HybridRetriever still accepts 'storage_facade' — "
            "split-brain not eliminated"
        )
        assert (
            "dao" in param_names
        ), "HybridRetriever must accept 'dao' (MemoryDAO) parameter"


# ===================================================================
# TEST 6: MemoryDAO Dual-Write Saga integrity (structural)
# ===================================================================


class TestDAODualWriteSagaIntegrity:
    """Structural verification that MemoryDAO's insert_memory still
    implements the Dual-Write Saga (B-7 pattern)."""

    def test_insert_memory_has_transaction_and_rollback(self):
        """MemoryDAO.insert_memory source must contain SAGA keywords."""
        import inspect

        source = inspect.getsource(MemoryDAO.insert_memory)

        assert (
            "transaction" in source
        ), "insert_memory must use transaction() for atomic SAGA"
        assert (
            "rollback" in source.lower()
        ), "insert_memory must have a rollback path for vector failure"
        assert (
            "upsert" in source.lower()
        ), "insert_memory must call vector upsert within the SAGA"

    def test_purge_memory_has_transaction_and_rollback(self):
        """MemoryDAO.purge_memory source must contain SAGA keywords."""
        import inspect

        source = inspect.getsource(MemoryDAO.purge_memory)

        assert (
            "transaction" in source
        ), "purge_memory must use transaction() for atomic SAGA"
        assert (
            "rollback" in source.lower()
        ), "purge_memory must have a compensating rollback"

    def test_dao_is_single_source_of_truth(self):
        """MemoryDAO must be the ONLY class with insert_memory capability
        in the mesa_storage package."""
        import inspect

        import mesa_storage

        # Walk all classes in mesa_storage
        storage_classes = []
        for name, obj in inspect.getmembers(mesa_storage, inspect.isclass):
            if hasattr(obj, "insert_memory"):
                storage_classes.append(name)

        assert storage_classes == ["MemoryDAO"], (
            f"Expected only MemoryDAO to have insert_memory, "
            f"found: {storage_classes}"
        )


# ===================================================================
# TEST 7: Novelty module has zero storage coupling
# ===================================================================


class TestNoveltyModulePurity:
    """Verify mesa_memory.valence.novelty has no storage imports."""

    def test_no_storage_imports(self):
        """novelty.py must not import from any storage module."""
        import inspect

        from mesa_memory.valence import novelty

        source = inspect.getsource(novelty)

        assert "mesa_memory.storage" not in source
        assert "mesa_storage" not in source
        assert "StorageFacade" not in source
        assert "RawLogStorage" not in source
        assert "VectorStorage" not in source
