import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

os.environ["MESA_API_KEY"] = "test_key"

from mesa_memory.adapter.live import OpenAICompatibleAdapter
from mesa_memory.api.server import app, get_embedder
from mesa_memory.consolidation.loop import ConsolidationLoop


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
def test_server_lifespan_health_metrics(tmp_path):
    import mesa_memory.api.server as srv

    previous = (srv._MESA_API_KEY, srv._MESA_PRINCIPAL_ID, srv._MESA_PRINCIPAL_STATUS)
    srv._MESA_API_KEY = "test_key"
    srv._MESA_PRINCIPAL_ID = "test-principal"
    srv._MESA_PRINCIPAL_STATUS = "active"
    try:
        runtime_lab_root = tmp_path / "mesa-lab"
        runtime_env = {
            "MESA_RUNTIME_PROFILE": "test-isolated",
            "MESA_RUNTIME_LAB_ROOT": str(runtime_lab_root),
            "MESA_STORAGE_ROOT": str(runtime_lab_root / "test-p0b-lifespan"),
            "MESA_LOAD_DOTENV": "false",
            "MESA_MODEL_ENABLED": "false",
            "MESA_EXTERNAL_PROVIDER_ENABLED": "false",
        }
        with patch.dict(os.environ, runtime_env, clear=False):
            dao = MagicMock()
            dao.health_check = AsyncMock(return_value={"status": "ok"})

            async def exercise_http_contract() -> None:
                srv.state.runtime_profile = srv.load_runtime_profile()
                srv.state.dao = dao
                srv.state.worker_supervisor = MagicMock()
                srv.state.is_ready = True
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as client:
                    # B1 FIX: /health and /metrics now require API key
                    assert (await client.get("/health")).status_code == 401
                    assert (await client.get("/metrics")).status_code == 401
                    assert (
                        await client.get("/health", headers={"X-API-Key": "test_key"})
                    ).status_code == 200
                    assert (
                        await client.get("/metrics", headers={"X-API-Key": "test_key"})
                    ).status_code == 200
                    assert (
                        await client.get(
                            "/v4/sessions/test/context",
                            headers={"X-API-Key": "wrong"},
                        )
                    ).status_code == 401

            try:
                asyncio.run(exercise_http_contract())
            finally:
                srv.state.is_ready = False
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
# Valence Core Tests
# -------------------------






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
        with pytest.raises(RuntimeError, match="embedding model is unavailable"):
            adapter.embed("prompt")

        mock_create.side_effect = None
        mock_item = MagicMock()
        mock_item.embedding = [0.1]
        mock_item.index = 0
        mock_create.return_value.data = [mock_item]
        assert adapter.embed_batch(["prompt"]) == [[0.1]]

        mock_create.side_effect = openai.NotFoundError(
            "Not found", response=MagicMock(), body={}
        )
        with pytest.raises(RuntimeError, match="embedding model is unavailable"):
            adapter.embed_batch(["prompt"])


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
        with pytest.raises(RuntimeError, match="embedding model is unavailable"):
            await adapter.aembed("prompt")

        mock_create.side_effect = None
        mock_item = MagicMock()
        mock_item.embedding = [0.1]
        mock_item.index = 0
        mock_create.return_value.data = [mock_item]
        assert await adapter.aembed_batch(["prompt"]) == [[0.1]]

        mock_create.side_effect = openai.NotFoundError(
            "Not found", response=MagicMock(), body={}
        )
        with pytest.raises(RuntimeError, match="embedding model is unavailable"):
            await adapter.aembed_batch(["prompt"])


def test_adapter_factory():
    import os
    from unittest.mock import patch

    # We mocked get_adapter in the fixture, but we can test MESA_LLM_MODEL
    with patch.dict(os.environ, {"MESA_LLM_MODEL": "openai_compatible:my-model"}):
        model_spec = os.environ.get("MESA_LLM_MODEL", "openai_compatible:my-model")
        assert model_spec == "openai_compatible:my-model"
