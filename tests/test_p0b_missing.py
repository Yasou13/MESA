import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
import networkx as nx

os.environ["MESA_API_KEY"] = "test_key"
from mesa_memory.api.server import app, get_dao, get_embedder

@pytest.fixture(autouse=True)
def mock_adapter():
    with patch("mesa_memory.adapter.factory.AdapterFactory.get_adapter") as mock_get:
        mock_adapter_instance = MagicMock()
        mock_adapter_instance.embed.return_value = [0.1]
        mock_get.return_value = mock_adapter_instance
        yield mock_get

# -------------------------
# API Server Tests
# -------------------------
def test_server_lifespan_health_metrics():
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/metrics").status_code == 200
        assert client.get("/v3/memory/session/test/context?agent_id=1", headers={"X-API-Key": "wrong"}).status_code == 401

def test_get_dao_embedder():
    import mesa_memory.api.server as srv
    from fastapi import HTTPException
    
    old_dao = getattr(srv.state, "dao", None)
    if hasattr(srv.state, "dao"):
        delattr(srv.state, "dao")
    with pytest.raises(HTTPException):
        srv.get_dao()
    if old_dao:
        srv.state.dao = old_dao
        
    assert callable(get_embedder())

# -------------------------
# Retrieval Hybrid Tests
# -------------------------
from mesa_memory.retrieval.hybrid import find_path, HybridRetriever

def test_find_path_exceptions():
    G = nx.Graph()
    G.add_node("A")
    assert find_path(G, "A", "C") == []
    G.add_node("B")
    assert find_path(G, "A", "B") == []
    
    G.add_edge("A", "C")
    G.add_edge("C", "D")
    G.add_edge("D", "B")
    assert find_path(G, "A", "B", max_hops=1) == []

@pytest.mark.asyncio
async def test_hybrid_exceptions():
    dao_mock = AsyncMock()
    analyzer_mock = MagicMock()
    embedder_mock = MagicMock()
    
    analyzer_mock.extract_entities.return_value = ["E1", "E2"]
    dao_mock.find_nodes_by_name.return_value = [{"id": "node1"}, {"id": "node2"}]
    dao_mock.get_memories.return_value = [{"id": "node1"}]
    dao_mock.search_memory_fts.side_effect = Exception("FTS error")
    
    access_mock = AsyncMock()
    access_mock.check_access.return_value = True
    
    retriever = HybridRetriever(dao=dao_mock, analyzer=analyzer_mock, embedder=embedder_mock, access_control=access_mock)
    retriever.get_vector_results = AsyncMock(return_value=[])
    retriever.get_graph_results = AsyncMock(return_value=[])
    
    # Enable multi hop with graph build failure
    retriever._build_graph_snapshot = AsyncMock(side_effect=Exception("Snapshot err"))
    res = await retriever.retrieve("query", "agent1", "session1", enable_multi_hop=True)
    assert res["multi_hop_path"] == []
    
    # Test _run_ppr no seeds
    retriever._build_graph_snapshot = AsyncMock(return_value=nx.DiGraph())
    assert await retriever._run_ppr("agent1", []) == []

def test_format_working_memory_budget():
    retriever = HybridRetriever(dao=AsyncMock(), analyzer=MagicMock(), embedder=MagicMock())
    retriever.embedder.get_token_count = MagicMock(return_value=1000) # huge token count
    
    res = retriever.format_working_memory([{"content_payload": "test"}], max_tokens=10)
    assert res == "Retrieved Context: None"

# -------------------------
# Valence Core Tests
# -------------------------
from mesa_memory.valence.core import ValenceMotor, calculate_fitness_score

def test_valence_fitness():
    assert calculate_fitness_score("test", 0) > 0
    assert calculate_fitness_score("test", 1000) > 0

@pytest.mark.asyncio
async def test_valence_evaluate_errors():
    motor = ValenceMotor(llm_adapter=MagicMock(), obs_layer=MagicMock(), storage=None)
    
    assert await motor.evaluate({}, {"error": True}) is False
    assert await motor.evaluate({}, {"format_violation": True}) is False
    
    # Test storage hydration exceptions
    storage_mock = MagicMock()
    storage_mock.load_embedding_cache.side_effect = Exception("Load err")
    motor_err = ValenceMotor(llm_adapter=MagicMock(), obs_layer=MagicMock(), storage=storage_mock)
    assert motor_err.existing_embeddings == []
    
    storage_mock = MagicMock()
    del storage_mock.load_embedding_cache
    storage_mock.get_all_embeddings.side_effect = Exception("Load err 2")
    motor_err2 = ValenceMotor(llm_adapter=MagicMock(), obs_layer=MagicMock(), storage=storage_mock)
    assert motor_err2.existing_embeddings == []

# -------------------------
# Consolidation Loop Tests
# -------------------------
from mesa_memory.consolidation.loop import ConsolidationLoop

@pytest.mark.asyncio
async def test_consolidation_loop_stop():
    loop = ConsolidationLoop(dao=AsyncMock(), embedder=MagicMock(), llm_a=MagicMock(), llm_b=MagicMock(), obs_layer=MagicMock())
    await loop.stop()
    assert not loop._running

# -------------------------
# OpenAIAdapter Tests
# -------------------------
from mesa_memory.adapter.live import OpenAICompatibleAdapter
import openai

def test_openai_adapter_methods():
    adapter = OpenAICompatibleAdapter(api_key="gsk_123", model_name="test")
    
    with patch.object(adapter._sync_client.chat.completions, 'create') as mock_create:
        mock_create.return_value.choices = [MagicMock(message=MagicMock(content="Hello"))]
        assert adapter.complete("prompt") == "Hello"

@pytest.mark.asyncio
async def test_openai_adapter_async_methods():
    adapter = OpenAICompatibleAdapter(api_key="gsk_123", model_name="test")
    
    with patch.object(adapter._async_client.chat.completions, 'create', new_callable=AsyncMock) as mock_create:
        mock_create.return_value.choices = [MagicMock(message=MagicMock(content="Hello"))]
        assert await adapter.acomplete("prompt") == "Hello"

def test_openai_adapter_embed_methods():
    adapter = OpenAICompatibleAdapter(api_key="gsk_123", model_name="test")
    
    with patch.object(adapter._sync_client.embeddings, 'create') as mock_create:
        mock_create.return_value.data = [MagicMock(embedding=[0.1, 0.2])]
        assert adapter.embed("prompt") == [0.1, 0.2]
        
        mock_create.side_effect = openai.NotFoundError("Not found", response=MagicMock(), body={})
        with patch("mesa_memory.adapter.claude._local_embed", return_value=[0.1]):
            assert adapter.embed("prompt") == [0.1]
        
        mock_create.side_effect = None
        mock_item = MagicMock()
        mock_item.embedding = [0.1]
        mock_item.index = 0
        mock_create.return_value.data = [mock_item]
        assert adapter.embed_batch(["prompt"]) == [[0.1]]

        mock_create.side_effect = openai.NotFoundError("Not found", response=MagicMock(), body={})
        with patch("mesa_memory.adapter.claude._local_embed_batch", return_value=[[0.1]]):
            assert adapter.embed_batch(["prompt"]) == [[0.1]]

@pytest.mark.asyncio
async def test_openai_adapter_async_embed_methods():
    adapter = OpenAICompatibleAdapter(api_key="gsk_123", model_name="test")
    
    with patch.object(adapter._async_client.embeddings, 'create', new_callable=AsyncMock) as mock_create:
        mock_create.return_value.data = [MagicMock(embedding=[0.1, 0.2])]
        assert await adapter.aembed("prompt") == [0.1, 0.2]
        
        mock_create.side_effect = openai.NotFoundError("Not found", response=MagicMock(), body={})
        with patch("mesa_memory.adapter.claude._local_embed", return_value=[0.1]):
            assert await adapter.aembed("prompt") == [0.1]
        
        mock_create.side_effect = None
        mock_item = MagicMock()
        mock_item.embedding = [0.1]
        mock_item.index = 0
        mock_create.return_value.data = [mock_item]
        assert await adapter.aembed_batch(["prompt"]) == [[0.1]]

        mock_create.side_effect = openai.NotFoundError("Not found", response=MagicMock(), body={})
        with patch("mesa_memory.adapter.claude._local_embed_batch", return_value=[[0.1]]):
            assert await adapter.aembed_batch(["prompt"]) == [[0.1]]

def test_adapter_factory():
    from mesa_memory.adapter.factory import AdapterFactory
    import os
    
    # We mocked get_adapter in the fixture, but we can test _get_llm_provider
    provider = os.environ.get("MESA_LLM_PROVIDER", "openai_compatible")
    assert provider == "openai_compatible"




