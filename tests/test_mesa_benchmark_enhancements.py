import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../mesa-benchmark")))
from mesa_benchmark.clients.mesa_client import MesaClientAdapter
from mesa_workers.ingestion_worker import IngestionWorker, IngestionTask

@pytest.mark.asyncio
async def test_mesa_client_adapter_initialization():
    adapter = MesaClientAdapter()
    assert adapter.enable_multi_hop is True
    assert adapter.top_n == 5
    
    # Mocking initialization to prevent real db creation
    with patch("mesa_benchmark.clients.mesa_client.AsyncEngine") as mock_sqlite, \
         patch("mesa_benchmark.clients.mesa_client.VectorEngine") as mock_vector, \
         patch("mesa_benchmark.clients.mesa_client.KuzuGraphProvider") as mock_graph, \
         patch("mesa_benchmark.clients.mesa_client.initialize_schema") as mock_init_schema, \
         patch("mesa_benchmark.clients.mesa_client.kuzu_initialize_schema") as mock_kuzu_schema:
         
        adapter.initialize({"enable_multi_hop": False, "top_n": 10})
        assert adapter.enable_multi_hop is False
        assert adapter.top_n == 10

@pytest.mark.asyncio
async def test_mesa_client_adapter_ingest():
    adapter = MesaClientAdapter()
    adapter.memory_dao = AsyncMock()
    
    with patch("mesa_benchmark.clients.mesa_client.asyncio.run_coroutine_threadsafe") as mock_run:
        mock_run.return_value.result.return_value = None
        adapter.ingest_context([MagicMock(context_id="test1", text="text1")])

@pytest.mark.asyncio
async def test_mesa_client_adapter_answer():
    adapter = MesaClientAdapter()
    adapter.retriever = AsyncMock()
    adapter.retriever.retrieve.return_value = []
    adapter.llm_adapter = AsyncMock()
    adapter.llm_adapter.acomplete.return_value = "Answer"
    
    with patch("mesa_benchmark.clients.mesa_client.asyncio.run_coroutine_threadsafe") as mock_run:
        mock_run.return_value.result.return_value = MagicMock(answer="Answer", references=[])
        res = adapter.answer(MagicMock(question="Q?"))
        assert res is not None

@pytest.mark.asyncio
async def test_ingestion_worker_process():
    worker = IngestionWorker(num_workers=1)
    task = IngestionTask(agent_id="agent1", text="text1")
    worker.dao = AsyncMock()
    worker.vector_engine = AsyncMock()
    worker.graph_provider = AsyncMock()
    worker.llm_adapter = AsyncMock()
    worker._entity_resolver = AsyncMock()
    worker._entity_resolver.resolve_entities.return_value = []
    
    with patch.object(worker, "process_cold_path", new_callable=AsyncMock) as mock_process:
        await worker._process_task(task)
        mock_process.assert_called_once()
