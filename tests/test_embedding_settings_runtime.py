"""P3-03: embedding configuration through real runtime and HTTP composition."""

import asyncio
import json
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from mesa_memory.api import server
from mesa_memory.config import config, configured_embedding_identity
from mesa_memory.embedding import service
from mesa_storage.projection_generations import (
    ProjectionGenerationIdentityMismatchError,
)
from tests.test_model_optional_runtime import (
    _assert_committed,
    _consume,
    _insert,
    _lanes,
    _search,
)


@pytest.fixture
def embedding_runtime(tmp_path, monkeypatch):
    calls = []
    unavailable = False

    def fake_server(request):
        if unavailable:
            raise httpx.ConnectError("embedding server unreachable", request=request)
        assert request.url.path == "/v1/embeddings"
        payload = json.loads(request.content)
        calls.append(payload)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]}],
                "model": payload["model"],
            },
        )

    class FakeSDKClient:
        def __init__(self, **options):
            self.client = httpx.Client(
                base_url=options["base_url"], transport=httpx.MockTransport(fake_server)
            )
            self.embeddings = SimpleNamespace(create=self.create)

        def create(self, **payload):
            response = self.client.post("embeddings", json=payload)
            response.raise_for_status()
            return SimpleNamespace(data=[SimpleNamespace(**response.json()["data"][0])])

    class FakeAsyncSDKClient(FakeSDKClient):
        async def create(self, **payload):
            return super().create(**payload)

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=FakeSDKClient, AsyncOpenAI=FakeAsyncSDKClient),
    )

    # Keep the old local default executable in the pre-change run, without
    # downloads. The tested Ollama path still uses the real HTTP backend/SDK.
    class LocalModel:
        def __init__(self, *args, **kwargs):
            pass

        def encode(self, text, **kwargs):
            return [1.0] + [0.0] * (config.embedding_dimension - 1)

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=LocalModel),
    )
    for name in (
        "MESA_EMBEDDING_DIMENSION",
        "MESA_EMBEDDING_BASE_URL",
        "MESA_EMBEDDING_API_KEY",
        "MESA_LLM_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        "MESA_RUNTIME_PROFILE": "combined",
        "MESA_STORAGE_ROOT": str(tmp_path / "storage"),
        "MESA_LOAD_DOTENV": "false",
        "MESA_MODEL_ENABLED": "false",
        "MESA_EXTRACTION_ENABLED": "false",
        "MESA_EXTERNAL_PROVIDER_ENABLED": "false",
        "MESA_EMBEDDING_MODEL": "ollama:qwen3-embedding:4b",
        "MESA_OLLAMA_URL": "http://127.0.0.1:11434",
        "MESA_API_KEY": "model-test-key",
        "MESA_PRINCIPAL_ID": "model-principal",
        "MESA_PRINCIPAL_STATUS": "active",
    }.items():
        monkeypatch.setenv(name, value)

    async def hold_polling(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(server, "_run_combined_durable_consumer", hold_polling)

    @asynccontextmanager
    async def runtime(**flags):
        for name, value in flags.items():
            monkeypatch.setenv(name, str(value))
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(server.lifespan(server.app))
            dao = server.state.dao
            await dao.ensure_v4_catalog_scope(
                tenant_id="model-tenant", workspace_id="default", dataset_id="main"
            )
            access = server.state.access_control
            await access.grant_principal_permission(
                "model-principal", "model-agent", "SESSION_CREATE"
            )
            await access.grant_scope_role(
                "model-principal",
                tenant_id="model-tenant",
                workspace_id="default",
                dataset_id="main",
                role="WRITER",
            )

            async def client():
                return await stack.enter_async_context(
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=server.app),
                        base_url="http://test",
                        headers={"X-API-Key": "model-test-key"},
                    )
                )

            env = SimpleNamespace(dao=dao, client=await client(), calls=calls)
            started = await env.client.post(
                "/v4/sessions/start",
                json={
                    "tenant_id": "model-tenant",
                    "workspace_id": "default",
                    "dataset_ids": ["main"],
                    "agent_id": "model-agent",
                },
            )
            assert started.status_code == 201, started.text
            env.session_id = started.json()["session_id"]

            async def restart(**new_flags):
                await stack.aclose()
                for name, value in new_flags.items():
                    monkeypatch.setenv(name, str(value))
                await stack.enter_async_context(server.lifespan(server.app))
                env.dao, env.client = server.state.dao, await client()

            env.restart = restart
            yield env

    def disconnect():
        nonlocal unavailable
        unavailable = True

    runtime.disconnect = disconnect
    yield runtime
    service.set_global_embedding_service(None)


@pytest.mark.asyncio
async def test_loopback_ollama_detects_dimension_once_and_admits_it(embedding_runtime):
    async with embedding_runtime(MESA_EMBEDDING_ENABLED="true") as env:
        assert configured_embedding_identity().dimension == 6
        assert server.state.vector_engine.semantic_operational is True
        assert env.calls == [
            {"model": "qwen3-embedding:4b", "input": "MESA embedding dimension probe"}
        ]
        await _insert(env)
        mutation = await env.dao.get_mutation("model-agent", env.mutation_id)
        assert mutation["embedding_dimension"] == 6
        assert mutation["embedding_provider"] == "ollama"
        assert mutation["embedding_model"] == "qwen3-embedding:4b"
        assert len(env.calls) == 1  # Admission must not probe again.
        await _consume(env, 3)
        await _assert_committed(env)
        assert (await _lanes(env))["VECTOR"] == "COMPLETED"
        assert (await _search(env))["vector_lane"]["search_executed"]
        assert len(env.calls) == 3  # One probe, one document, one query.


@pytest.mark.asyncio
async def test_remote_ollama_startup_requires_external_permission(embedding_runtime):
    with pytest.raises(
        ValueError, match="[Rr]emote Ollama.*MESA_EXTERNAL_PROVIDER_ENABLED"
    ):
        async with embedding_runtime(
            MESA_EMBEDDING_ENABLED="true",
            MESA_OLLAMA_URL="http://embedding.example:11434",
        ):
            pass


@pytest.mark.asyncio
async def test_unreachable_dimension_probe_names_model_and_url(embedding_runtime):
    embedding_runtime.disconnect()
    with pytest.raises(service.EmbeddingUnavailableError) as error:
        async with embedding_runtime(MESA_EMBEDDING_ENABLED="true"):
            pass
    assert "qwen3-embedding:4b" in str(error.value)
    assert "http://127.0.0.1:11434/v1" in str(error.value)


@pytest.mark.asyncio
async def test_vector_free_records_adopt_new_model_and_backfill(embedding_runtime):
    async with embedding_runtime(MESA_EMBEDDING_ENABLED="false") as env:
        assert configured_embedding_identity().dimension == 768
        assert env.calls == []
        await _insert(env)
        await _consume(env, 3)
        await _assert_committed(env)
        assert (await _lanes(env))["VECTOR"] == "SKIPPED"
        await env.restart(
            MESA_EMBEDDING_ENABLED="true", MESA_EMBEDDING_MODEL="ollama:new-model:tag"
        )
        assert configured_embedding_identity().model == "new-model:tag"
        assert (await _lanes(env))["VECTOR"] == "PENDING"
        await _assert_committed(env)
        await _search(env)
        await _consume(env)
        assert (await _lanes(env))["VECTOR"] == "COMPLETED"
        result = await _search(env)
        assert result["vector_lane"]["search_executed"]
        assert result["degraded_sources"] == []


@pytest.mark.asyncio
async def test_existing_vectors_refuse_new_model_and_require_rebuild(embedding_runtime):
    async with embedding_runtime(
        MESA_EMBEDDING_ENABLED="true", MESA_EMBEDDING_DIMENSION="6"
    ) as env:
        assert env.calls == []  # An explicit dimension skips the startup probe.
        await _insert(env)
        await _consume(env, 3)
        await _assert_committed(env)
        assert (await _lanes(env))["VECTOR"] == "COMPLETED"
        with pytest.raises(
            ProjectionGenerationIdentityMismatchError, match="rebuild is required"
        ):
            await env.restart(MESA_EMBEDDING_MODEL="ollama:new-model:tag")


def test_rebuild_detects_dimension_before_building_manifest(
    embedding_runtime, monkeypatch
):
    from mesa_memory import rebuild_runner

    monkeypatch.setenv("MESA_EMBEDDING_ENABLED", "true")
    providers = rebuild_runner._provider_runtime()
    assert providers.manifest["model"] == "qwen3-embedding:4b"
    assert providers.manifest["dimension"] == 6
    assert configured_embedding_identity().dimension == 6
    assert providers.embedding_service.dimension == 6


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("local:cached-model", ("local", "cached-model")),
        ("ollama:qwen3-embedding:4b", ("ollama", "qwen3-embedding:4b")),
        ("openai:text-embedding-3-small", ("openai", "text-embedding-3-small")),
        (
            "openai_compatible:nvidia/nemotron-3-embed-1b",
            ("openai_compatible", "nvidia/nemotron-3-embed-1b"),
        ),
    ],
)
def test_embedding_spec_reuses_provider_model_parsing(spec, expected):
    from mesa_memory.config import parse_embedding_spec

    assert parse_embedding_spec(spec) == expected


@pytest.mark.parametrize(
    "spec", ["", "local", "local:", "ollama:", "mock:test", "unknown:model"]
)
def test_invalid_embedding_spec_names_setting_and_supported_providers(spec):
    from mesa_memory.config import parse_embedding_spec

    with pytest.raises(
        ValueError,
        match="MESA_EMBEDDING_MODEL.*local, ollama, openai, openai_compatible",
    ):
        parse_embedding_spec(spec)


@pytest.mark.asyncio
async def test_ollama_override_url_is_fenced_before_backend_composition(
    embedding_runtime, monkeypatch
):
    monkeypatch.delitem(sys.modules, "openai", raising=False)
    with pytest.raises(
        ValueError, match="Remote Ollama.*MESA_EXTERNAL_PROVIDER_ENABLED"
    ):
        async with embedding_runtime(
            MESA_EMBEDDING_ENABLED="true",
            MESA_EMBEDDING_BASE_URL="https://remote.example/v1",
        ):
            pass
