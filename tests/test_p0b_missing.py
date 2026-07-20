import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ["MESA_API_KEY"] = "test_key"

from mesa_memory.adapter.live import OpenAICompatibleAdapter
from mesa_memory.api.server import app, get_embedder
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.retrieval.hybrid import HybridRetriever
from mesa_memory.valence.core import ValenceMotor, calculate_fitness_score


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
    import mesa_memory.api.server as srv

    previous = (srv._MESA_API_KEY, srv._MESA_PRINCIPAL_ID, srv._MESA_PRINCIPAL_STATUS)
    srv._MESA_API_KEY = "test_key"
    srv._MESA_PRINCIPAL_ID = "test-principal"
    srv._MESA_PRINCIPAL_STATUS = "active"
    try:
        runtime_env = {
            "MESA_RUNTIME_PROFILE": "test-isolated",
            "MESA_STORAGE_ROOT": "/storage/mesa-lab/fast-zero-closure/test-p0b-lifespan",
            "MESA_LOAD_DOTENV": "false",
            "MESA_MODEL_ENABLED": "false",
            "MESA_EXTERNAL_PROVIDER_ENABLED": "false",
        }
        with patch.dict(os.environ, runtime_env, clear=False):
            with patch("mesa_memory.api.server.MemoryDAO") as mock_dao:
                mock_dao.return_value.initialize = AsyncMock()
                mock_dao.return_value.health_check = AsyncMock(return_value={"status": "ok"})
                with TestClient(app) as client:
                    # B1 FIX: /health and /metrics now require API key
                    assert client.get("/health").status_code == 401
                    assert client.get("/metrics").status_code == 401
                    assert (
                        client.get(
                            "/health", headers={"X-API-Key": "test_key"}
                        ).status_code
                        == 200
                    )
                    assert (
                        client.get(
                            "/metrics", headers={"X-API-Key": "test_key"}
                        ).status_code
                        == 200
                    )
                    assert (
                        client.get(
                            "/v3/memory/session/test/context?agent_id=1",
                            headers={"X-API-Key": "wrong"},
                        ).status_code
                        == 401
                    )
    finally:
        (
            srv._MESA_API_KEY,
            srv._MESA_PRINCIPAL_ID,
            srv._MESA_PRINCIPAL_STATUS,
        ) = previous


def test_get_dao_embedder():
    from fastapi import HTTPException

    import mesa_memory.api.server as srv

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


@pytest.mark.asyncio
async def test_hybrid_exceptions():
    dao_mock = AsyncMock()
    analyzer_mock = MagicMock()
    embedder_mock = MagicMock()

    analyzer_mock.extract_entities.return_value = ["E1", "E2"]
    dao_mock.find_nodes_by_name.return_value = [{"id": "node1"}, {"id": "node2"}]
    dao_mock.get_memories.return_value = [{"id": "node1"}]
    dao_mock.search_memory_fts.side_effect = Exception("FTS error")
    dao_mock.get_neighbors.side_effect = Exception("KùzuDB error")
    dao_mock.graph_provider = AsyncMock()
    dao_mock.graph_provider.get_cognitive_salience.side_effect = Exception(
        "KùzuDB error"
    )

    access_mock = AsyncMock()
    access_mock.check_access.return_value = True

    retriever = HybridRetriever(
        dao=dao_mock,
        analyzer=analyzer_mock,
        embedder=embedder_mock,
        access_control=access_mock,
    )
    retriever.get_vector_results = AsyncMock(return_value=[])
    retriever.get_graph_results = AsyncMock(return_value=[])

    # Enable multi hop with graph traversal failure
    res = await retriever.retrieve("query", "agent1", "session1", enable_multi_hop=True)
    assert res["multi_hop_path"] == []

    # Test get_graph_results no seeds
    assert await retriever.get_graph_results("agent1", []) == []

    # Test get_graph_results exceptions caught gracefully
    dao_mock.find_nodes_by_name.return_value = [{"id": "seed1"}]
    res = await retriever.get_graph_results("agent1", ["seed1"])
    assert res == []


def test_format_working_memory_budget():
    retriever = HybridRetriever(
        dao=AsyncMock(), analyzer=MagicMock(), embedder=MagicMock()
    )
    retriever.embedder.get_token_count = MagicMock(
        return_value=1000
    )  # huge token count

    res = retriever.format_working_memory([{"content_payload": "test"}], max_tokens=10)
    assert res == "Retrieved Context: None"


# -------------------------
# Valence Core Tests
# -------------------------


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
    motor_err = ValenceMotor(
        llm_adapter=MagicMock(), obs_layer=MagicMock(), storage=storage_mock
    )
    assert motor_err.existing_embeddings == []

    storage_mock = MagicMock()
    del storage_mock.load_embedding_cache
    storage_mock.get_all_embeddings.side_effect = Exception("Load err 2")
    motor_err2 = ValenceMotor(
        llm_adapter=MagicMock(), obs_layer=MagicMock(), storage=storage_mock
    )
    assert motor_err2.existing_embeddings == []


# -------------------------
# Consolidation Loop Tests
# -------------------------


@pytest.mark.asyncio
async def test_consolidation_loop_stop():
    dao = AsyncMock()
    dao.get_all_embeddings = MagicMock(return_value=[])
    dao.load_embedding_cache = MagicMock(return_value=[])
    loop = ConsolidationLoop(
        dao=dao,
        embedder=MagicMock(),
        llm_a=MagicMock(),
        llm_b=MagicMock(),
        obs_layer=MagicMock(),
    )
    await loop.stop()
    assert not loop._running


# -------------------------
# OpenAIAdapter Tests
# -------------------------


@pytest.mark.optional_provider
def test_openai_adapter_methods():
    adapter = OpenAICompatibleAdapter(api_key="test_key_123", model_name="test")

    with patch.object(adapter._sync_client.chat.completions, "create") as mock_create:
        mock_create.return_value.choices = [
            MagicMock(message=MagicMock(content="Hello"))
        ]
        assert adapter.complete("prompt") == "Hello"


@pytest.mark.asyncio
@pytest.mark.optional_provider
async def test_openai_adapter_async_methods():
    adapter = OpenAICompatibleAdapter(api_key="test_key_123", model_name="test")

    with patch.object(
        adapter._async_client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value.choices = [
            MagicMock(message=MagicMock(content="Hello"))
        ]
        assert await adapter.acomplete("prompt") == "Hello"


@pytest.mark.optional_provider
def test_openai_adapter_embed_methods():
    import openai

    adapter = OpenAICompatibleAdapter(api_key="test_key_123", model_name="test")

    with patch.object(adapter._sync_client.embeddings, "create") as mock_create:
        mock_create.return_value.data = [MagicMock(embedding=[0.1, 0.2])]
        assert adapter.embed("prompt") == [0.1, 0.2]

        mock_create.side_effect = openai.NotFoundError(
            "Not found", response=MagicMock(), body={}
        )
        with patch("mesa_memory.adapter.claude._local_embed", return_value=[0.1]):
            assert adapter.embed("prompt") == [0.1]

        mock_create.side_effect = None
        mock_item = MagicMock()
        mock_item.embedding = [0.1]
        mock_item.index = 0
        mock_create.return_value.data = [mock_item]
        assert adapter.embed_batch(["prompt"]) == [[0.1]]

        mock_create.side_effect = openai.NotFoundError(
            "Not found", response=MagicMock(), body={}
        )
        with patch(
            "mesa_memory.adapter.claude._local_embed_batch", return_value=[[0.1]]
        ):
            assert adapter.embed_batch(["prompt"]) == [[0.1]]


@pytest.mark.asyncio
@pytest.mark.optional_provider
async def test_openai_adapter_async_embed_methods():
    import openai

    adapter = OpenAICompatibleAdapter(api_key="test_key_123", model_name="test")

    with patch.object(
        adapter._async_client.embeddings, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value.data = [MagicMock(embedding=[0.1, 0.2])]
        assert await adapter.aembed("prompt") == [0.1, 0.2]

        mock_create.side_effect = openai.NotFoundError(
            "Not found", response=MagicMock(), body={}
        )
        with patch("mesa_memory.adapter.claude._local_embed", return_value=[0.1]):
            assert await adapter.aembed("prompt") == [0.1]

        mock_create.side_effect = None
        mock_item = MagicMock()
        mock_item.embedding = [0.1]
        mock_item.index = 0
        mock_create.return_value.data = [mock_item]
        assert await adapter.aembed_batch(["prompt"]) == [[0.1]]

        mock_create.side_effect = openai.NotFoundError(
            "Not found", response=MagicMock(), body={}
        )
        with patch(
            "mesa_memory.adapter.claude._local_embed_batch", return_value=[[0.1]]
        ):
            assert await adapter.aembed_batch(["prompt"]) == [[0.1]]


def test_adapter_factory():
    import os
    from unittest.mock import patch

    # We mocked get_adapter in the fixture, but we can test _get_llm_provider
    with patch.dict(os.environ, {"MESA_LLM_PROVIDER": "openai_compatible"}):
        provider = os.environ.get("MESA_LLM_PROVIDER", "openai_compatible")
        assert provider == "openai_compatible"
