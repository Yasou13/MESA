"""Tests for Canonical EmbeddingService, Truthful Identity, and Egress Fence."""

import json
import math
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.config import (
    NEMOTRON_EMBEDDING_DIMENSION,
    NEMOTRON_EMBEDDING_MODEL,
    NEMOTRON_EMBEDDING_VERSION,
    MesaConfig,
    config,
)
from mesa_memory.embedding.service import (
    EmbeddingGenerationError,
    EmbeddingIdentity,
    EmbeddingIdentityMismatchError,
    EmbeddingService,
    EmbeddingUnavailableError,
    ExternalProviderForbiddenError,
    _l2_normalize,
    get_embedding_service,
    set_global_embedding_service,
)
from mesa_storage.projection_generations import (
    ProjectionGenerationIdentityMismatchError,
    ProjectionGenerationRepository,
)
from mesa_storage.vector_engine import VectorEngine


@pytest.fixture
def fake_openai_embedding_sdk(monkeypatch):
    captured = {
        "sync_calls": [],
        "async_calls": [],
        "client_options": [],
        "dimension": NEMOTRON_EMBEDDING_DIMENSION,
        "sync_failure": None,
        "async_failure": None,
    }

    def response():
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[1.0] * captured["dimension"])]
        )

    class SyncEmbeddings:
        def create(self, **kwargs):
            captured["sync_calls"].append(kwargs)
            if captured["sync_failure"] is not None:
                raise captured["sync_failure"]
            return response()

    class AsyncEmbeddings:
        async def create(self, **kwargs):
            captured["async_calls"].append(kwargs)
            if captured["async_failure"] is not None:
                raise captured["async_failure"]
            return response()

    def sync_client(**options):
        captured["client_options"].append(("sync", options))
        return SimpleNamespace(embeddings=SyncEmbeddings())

    def async_client(**options):
        captured["client_options"].append(("async", options))
        return SimpleNamespace(embeddings=AsyncEmbeddings())

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=sync_client, AsyncOpenAI=async_client),
    )
    monkeypatch.setattr(config, "embedding_api_key", "embedding-test-key")
    monkeypatch.setattr(config, "embedding_base_url", "https://embedding.test/v1")
    yield captured
    set_global_embedding_service(None)


def _nemotron_identity(
    *,
    dimension: int = NEMOTRON_EMBEDDING_DIMENSION,
    version: str = NEMOTRON_EMBEDDING_VERSION,
) -> EmbeddingIdentity:
    return EmbeddingIdentity(
        provider="openai_compatible",
        model=NEMOTRON_EMBEDDING_MODEL,
        dimension=dimension,
        version=version,
    )


def _real_external_service(identity: EmbeddingIdentity) -> EmbeddingService:
    return get_embedding_service(
        identity=identity,
        external_enabled=True,
        force_refresh=True,
    )


def test_embedding_identity_space_id():
    ident = EmbeddingIdentity(
        provider="local",
        model="magibu/embeddingmagibu-200m",
        dimension=768,
        version="v1",
        normalized=True,
    )
    assert (
        ident.embedding_space_id == "local:magibu/embeddingmagibu-200m:v1:768:norm=true"
    )
    assert ident.dimension == 768
    assert ident.normalized is True


def test_embedding_space_id_changes_with_model_revision():
    v1 = EmbeddingIdentity(
        provider="local", model="model", dimension=768, model_revision="rev-1"
    )
    v2 = EmbeddingIdentity(
        provider="local", model="model", dimension=768, model_revision="rev-2"
    )
    assert v1.embedding_space_id != v2.embedding_space_id


def test_mock_embedding_service_sync_and_async():
    ident = EmbeddingIdentity(
        provider="mock",
        model="deterministic-mock",
        dimension=768,
        version="v1",
        normalized=True,
    )
    service = EmbeddingService(identity=ident)

    # Document embedding
    doc_vec = service.embed_document("Türkiye Büyük Millet Meclisi")
    assert len(doc_vec) == 768
    # Check L2 normalization
    norm = math.sqrt(sum(x * x for x in doc_vec))
    assert pytest.approx(norm, 1e-4) == 1.0

    # Query embedding
    q_vec = service.embed_query("TBMM yetkileri")
    assert len(q_vec) == 768

    # Batch embedding
    batch_vecs = service.embed_batch(["metin 1", "metin 2", "metin 3"])
    assert len(batch_vecs) == 3
    assert all(len(v) == 768 for v in batch_vecs)


@pytest.mark.asyncio
async def test_mock_embedding_service_async_methods():
    ident = EmbeddingIdentity(
        provider="mock",
        model="deterministic-mock",
        dimension=768,
        normalized=True,
    )
    service = EmbeddingService(identity=ident)

    doc_vec = await service.aembed_document("Anayasa Mahkemesi kararı")
    assert len(doc_vec) == 768

    q_vec = await service.aembed_query("iptal davası")
    assert len(q_vec) == 768

    batch_vecs = await service.aembed_batch(["madde 1", "madde 2"])
    assert len(batch_vecs) == 2


def test_fail_closed_on_unavailable_model_no_silent_fallback():
    """Missing model must fail closed with EmbeddingUnavailableError (no cross-family fallback)."""
    ident = EmbeddingIdentity(
        provider="sentence-transformers",
        model="nonexistent-model-xyz-12345",
        dimension=768,
        normalized=True,
    )
    # allow_model_loading=False simulates unavailable model on local disk
    service = EmbeddingService(identity=ident, allow_model_loading=False)

    with pytest.raises(
        EmbeddingUnavailableError, match="unavailable and no silent fallback"
    ):
        service.embed_document("test text")

    with pytest.raises(
        EmbeddingUnavailableError, match="unavailable and no silent fallback"
    ):
        service.embed_batch(["test 1", "test 2"])


def test_external_provider_egress_fence():
    """When external_enabled=False, external embedding providers are strictly forbidden."""
    ident = EmbeddingIdentity(
        provider="openai_compatible",
        model="text-embedding-3-small",
        dimension=1536,
    )

    # Should raise ExternalProviderForbiddenError immediately
    with pytest.raises(
        ExternalProviderForbiddenError, match="MESA_EXTERNAL_PROVIDER_ENABLED=false"
    ):
        EmbeddingService(identity=ident, external_enabled=False)

    # When external_enabled=True, construction succeeds
    service_allowed = EmbeddingService(
        identity=ident,
        external_enabled=True,
        provider_fn=lambda _t: [0.1] * 1536,
    )
    assert service_allowed.identity().dimension == 1536


def test_custom_provider_dimension_mismatch():
    """Provider returning wrong dimension triggers EmbeddingIdentityMismatchError."""
    ident = EmbeddingIdentity(
        provider="custom",
        model="test-model",
        dimension=768,
    )
    service = EmbeddingService(
        identity=ident,
        provider_fn=lambda _t: [0.1] * 384,  # Returns 384 instead of 768
    )

    with pytest.raises(
        EmbeddingIdentityMismatchError,
        match="does not match configured identity dimension",
    ):
        service.embed_document("sample text")


@pytest.mark.parametrize(
    "failure", [RuntimeError("HTTP 404"), TimeoutError("provider timeout")]
)
def test_provider_failure_never_returns_another_family(failure):
    identity = EmbeddingIdentity(
        provider="openai_compatible",
        model="configured-external-model",
        dimension=4,
    )

    def unavailable(_text):
        raise failure

    service = EmbeddingService(
        identity=identity,
        provider_fn=unavailable,
        external_enabled=True,
    )
    with pytest.raises(EmbeddingGenerationError):
        service.embed_document("must fail closed")


def test_local_cache_miss_never_attempts_download(monkeypatch):
    calls = []

    def missing_model(model, **kwargs):
        calls.append((model, kwargs))
        raise OSError("not cached")

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=missing_model),
    )
    identity = EmbeddingIdentity(
        provider="local", model="missing-local-model", dimension=4
    )
    service = EmbeddingService(identity=identity, allow_model_loading=True)

    with pytest.raises(EmbeddingUnavailableError):
        service.embed_document("no download")
    assert calls == [("missing-local-model", {"local_files_only": True})]


def test_local_loader_pins_the_configured_model_revision(monkeypatch):
    calls = []

    def missing_model(model, **kwargs):
        calls.append((model, kwargs))
        raise OSError("not cached")

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=missing_model),
    )
    identity = EmbeddingIdentity(
        provider="local", model="cached-model", dimension=4, model_revision="commit-abc"
    )
    EmbeddingService(identity=identity, allow_model_loading=True)

    assert calls == [
        ("cached-model", {"local_files_only": True, "revision": "commit-abc"})
    ]


def test_external_embedding_factory_composes_real_service_at_the_provider_boundary():
    identity = EmbeddingIdentity(
        provider="openai_compatible", model="text-embedding-3-small", dimension=4
    )
    calls = []

    def factory(composed_identity):
        calls.append(composed_identity)
        return (lambda _text: [0.25] * 4, lambda _text: [0.25] * 4)

    try:
        service = get_embedding_service(
            identity=identity,
            external_enabled=True,
            external_backend_factory=factory,
            force_refresh=True,
        )
        assert service.embed_document("composition") == [0.5] * 4
        assert calls == [identity]
    finally:
        set_global_embedding_service(None)


def test_nemotron_sync_document_request_uses_passage_role(fake_openai_embedding_sdk):
    service = _real_external_service(_nemotron_identity())

    assert len(service.embed_document("document")) == NEMOTRON_EMBEDDING_DIMENSION
    assert fake_openai_embedding_sdk["sync_calls"] == [
        {
            "model": NEMOTRON_EMBEDDING_MODEL,
            "input": "document",
            "extra_body": {"input_type": "passage", "truncate": "END"},
        }
    ]


def test_nemotron_sync_query_request_uses_query_role(fake_openai_embedding_sdk):
    service = _real_external_service(_nemotron_identity())

    assert len(service.embed_query("question")) == NEMOTRON_EMBEDDING_DIMENSION
    assert fake_openai_embedding_sdk["sync_calls"] == [
        {
            "model": NEMOTRON_EMBEDDING_MODEL,
            "input": "question",
            "extra_body": {"input_type": "query", "truncate": "END"},
        }
    ]


@pytest.mark.asyncio
async def test_nemotron_async_document_request_uses_passage_role(
    fake_openai_embedding_sdk,
):
    service = _real_external_service(_nemotron_identity())

    assert (
        len(await service.aembed_document("async document"))
        == NEMOTRON_EMBEDDING_DIMENSION
    )
    assert fake_openai_embedding_sdk["async_calls"] == [
        {
            "model": NEMOTRON_EMBEDDING_MODEL,
            "input": "async document",
            "extra_body": {"input_type": "passage", "truncate": "END"},
        }
    ]


@pytest.mark.asyncio
async def test_nemotron_async_query_request_uses_query_role(
    fake_openai_embedding_sdk,
):
    service = _real_external_service(_nemotron_identity())

    assert (
        len(await service.aembed_query("async question"))
        == NEMOTRON_EMBEDDING_DIMENSION
    )
    assert fake_openai_embedding_sdk["async_calls"] == [
        {
            "model": NEMOTRON_EMBEDDING_MODEL,
            "input": "async question",
            "extra_body": {"input_type": "query", "truncate": "END"},
        }
    ]


@pytest.mark.asyncio
async def test_nemotron_document_batches_use_passage_role(
    fake_openai_embedding_sdk,
):
    service = _real_external_service(_nemotron_identity())

    assert len(service.embed_batch(["one", "two"])) == 2
    assert len(await service.aembed_batch(["three", "four"])) == 2
    captured = (
        fake_openai_embedding_sdk["sync_calls"]
        + fake_openai_embedding_sdk["async_calls"]
    )
    assert [call["extra_body"] for call in captured] == [
        {"input_type": "passage", "truncate": "END"}
    ] * 4


def test_non_nemotron_openai_compatible_request_preserves_legacy_body(
    fake_openai_embedding_sdk,
):
    fake_openai_embedding_sdk["dimension"] = 4
    service = _real_external_service(
        EmbeddingIdentity(
            provider="openai_compatible",
            model="text-embedding-3-small",
            dimension=4,
        )
    )

    assert len(service.embed_query("legacy-compatible")) == 4
    assert fake_openai_embedding_sdk["sync_calls"] == [
        {"model": "text-embedding-3-small", "input": "legacy-compatible"}
    ]


def test_nemotron_wrong_dimension_fails_closed_before_request():
    with pytest.raises(
        EmbeddingIdentityMismatchError,
        match="requires configured identity dimension 2048",
    ):
        EmbeddingService(
            identity=_nemotron_identity(dimension=768),
            provider_fn=lambda _text: [0.0] * 768,
            external_enabled=True,
        )


def test_nemotron_requires_versioned_asymmetric_embedding_space():
    with pytest.raises(
        EmbeddingIdentityMismatchError, match=NEMOTRON_EMBEDDING_VERSION
    ):
        EmbeddingService(
            identity=_nemotron_identity(version="v1"),
            provider_fn=lambda _text: [0.0] * NEMOTRON_EMBEDDING_DIMENSION,
            external_enabled=True,
        )


def test_nemotron_environment_contract_rejects_legacy_dimension():
    with pytest.raises(ValueError, match="MESA_EMBEDDING_DIMENSION=2048"):
        MesaConfig(
            external_provider_enabled=True,
            embedding_provider="openai_compatible",
            external_embedding_model=NEMOTRON_EMBEDDING_MODEL,
            embedding_dimension=768,
            embedding_version=NEMOTRON_EMBEDDING_VERSION,
        )


def test_embedding_endpoint_overrides_llm_endpoint(fake_openai_embedding_sdk):
    _real_external_service(_nemotron_identity())

    assert fake_openai_embedding_sdk["client_options"] == [
        (
            "sync",
            {
                "api_key": "embedding-test-key",
                "base_url": "https://embedding.test/v1",
                "timeout": 20.0,
                "max_retries": 0,
            },
        ),
        (
            "async",
            {
                "api_key": "embedding-test-key",
                "base_url": "https://embedding.test/v1",
                "timeout": 20.0,
                "max_retries": 0,
            },
        ),
    ]


def test_embedding_endpoint_preserves_llm_endpoint_fallback(
    fake_openai_embedding_sdk, monkeypatch
):
    monkeypatch.setattr(config, "embedding_api_key", None)
    monkeypatch.setattr(config, "embedding_base_url", None)
    monkeypatch.setattr(config, "llm_api_key", "legacy-llm-key")
    monkeypatch.setattr(config, "llm_base_url", "https://legacy-llm.test/v1")

    _real_external_service(_nemotron_identity())

    captured_options = [
        options for _client, options in fake_openai_embedding_sdk["client_options"]
    ]
    assert captured_options == [
        {
            "api_key": "legacy-llm-key",
            "base_url": "https://legacy-llm.test/v1",
            "timeout": 20.0,
            "max_retries": 0,
        }
    ] * 2


def test_nemotron_provider_failure_preserves_fail_closed_error(
    fake_openai_embedding_sdk,
):
    fake_openai_embedding_sdk["sync_failure"] = TimeoutError("provider timeout")
    service = _real_external_service(_nemotron_identity())

    with pytest.raises(EmbeddingGenerationError, match="request failed"):
        service.embed_query("must fail closed")
    assert service.is_operational is False


@pytest.mark.asyncio
async def test_vector_engine_routes_document_and_query_to_distinct_service_roles(
    tmp_path,
):
    calls = []
    identity = EmbeddingIdentity(provider="custom", model="role-spy", dimension=2)

    async def document_provider(text):
        calls.append(("document", text))
        return [1.0, 0.0]

    async def query_provider(text):
        calls.append(("query", text))
        return [0.0, 1.0]

    service = EmbeddingService(
        identity=identity,
        async_provider_fn=document_provider,
        async_query_provider_fn=query_provider,
    )
    engine = VectorEngine(str(tmp_path / "vectors"), embedding_service=service)
    engine._initialized = True
    try:
        assert await engine.compute_embedding("memory") == [1.0, 0.0]
        assert await engine.compute_query_embedding("search") == [0.0, 1.0]
    finally:
        await engine.close()
    assert calls == [("document", "memory"), ("query", "search")]


@pytest.mark.asyncio
async def test_hybrid_retrieval_routes_search_text_to_query_embedding():
    from mesa_memory.retrieval.hybrid import HybridRetriever

    vector = SimpleNamespace(
        compute_query_embedding=AsyncMock(return_value=[0.0, 1.0]),
        compute_embedding=AsyncMock(side_effect=AssertionError("document route used")),
    )
    dao = SimpleNamespace(
        vector_engine=vector,
        search_memory=AsyncMock(return_value=[]),
    )
    retriever = object.__new__(HybridRetriever)
    retriever.dao = dao

    assert await retriever.get_vector_results("agent", "search text") == []
    vector.compute_query_embedding.assert_awaited_once_with("search text")
    vector.compute_embedding.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_custom_provider_functions_remain_query_compatible():
    sync_calls = []
    async_calls = []

    async def legacy_async(text):
        async_calls.append(text)
        return [1.0, 0.0]

    service = EmbeddingService(
        identity=EmbeddingIdentity(provider="custom", model="legacy", dimension=2),
        provider_fn=lambda text: sync_calls.append(text) or [1.0, 0.0],
        async_provider_fn=legacy_async,
    )

    assert service.embed_query("sync query") == [1.0, 0.0]
    assert await service.aembed_query("async query") == [1.0, 0.0]
    assert sync_calls == ["sync query"]
    assert async_calls == ["async query"]


@pytest.mark.asyncio
async def test_restart_fences_legacy_nemotron_request_semantics() -> None:
    legacy = EmbeddingIdentity(
        provider="openai_compatible",
        model=NEMOTRON_EMBEDDING_MODEL,
        dimension=NEMOTRON_EMBEDDING_DIMENSION,
        version="v1",
        model_revision="model-revision-42",
    )
    corrected = EmbeddingIdentity(
        provider="openai_compatible",
        model=NEMOTRON_EMBEDDING_MODEL,
        dimension=NEMOTRON_EMBEDDING_DIMENSION,
        version=NEMOTRON_EMBEDDING_VERSION,
        model_revision="model-revision-42",
    )
    # The durable manifest compares version independently even when an explicit
    # model revision makes the legacy space-id string itself identical.
    assert legacy.embedding_space_id == corrected.embedding_space_id

    class Cursor:
        async def fetchone(self):
            return {
                "active_generation_id": "legacy",
                "provider_manifest_json": json.dumps(legacy.as_dict()),
            }

    class Connection:
        async def execute(self, _query):
            return Cursor()

        async def commit(self):
            return None

    class Transaction:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return None

    class SQLiteEngine:
        def transaction(self):
            return Transaction()

    generations = ProjectionGenerationRepository(SQLiteEngine())  # type: ignore[arg-type]
    with pytest.raises(
        ProjectionGenerationIdentityMismatchError, match="rebuild is required"
    ):
        await generations.assert_active_embedding_identity(corrected.as_dict())


def test_configured_external_embedding_uses_the_production_factory(monkeypatch):
    from mesa_memory.config import config

    monkeypatch.setenv("MESA_EXTERNAL_PROVIDER_ENABLED", "true")
    monkeypatch.setattr(config, "embedding_provider", "openai_compatible")
    monkeypatch.setattr(config, "external_embedding_model", "configured-model")
    monkeypatch.setattr(config, "embedding_dimension", 4)
    constructed = []

    class FakeNetworkBoundary:
        def __init__(self, identity):
            constructed.append(identity)

        def embed_document(self, _text):
            return [0.25] * 4

        async def aembed_document(self, _text):
            return [0.25] * 4

        def embed_query(self, _text):
            return [0.25] * 4

        async def aembed_query(self, _text):
            return [0.25] * 4

    monkeypatch.setattr(
        "mesa_memory.embedding.service._OpenAICompatibleEmbeddingBackend",
        FakeNetworkBoundary,
    )
    try:
        service = get_embedding_service(force_refresh=True, external_enabled=True)
        assert service.embed_document("configured composition") == [0.5] * 4
        assert constructed[0].model == "configured-model"
    finally:
        set_global_embedding_service(None)


@pytest.mark.asyncio
async def test_vector_engine_never_self_composes_embedding_service(tmp_path):
    engine = VectorEngine(str(tmp_path / "vectors"), allow_model_loading=True)
    engine._initialized = True

    assert engine.embedding_identity is None
    with pytest.raises(RuntimeError, match="no canonical embedding service"):
        await engine.compute_embedding("must require explicit composition")


def test_l2_normalization_helper():
    raw = [3.0, 4.0]
    normed = _l2_normalize(raw)
    assert pytest.approx(normed[0]) == 0.6
    assert pytest.approx(normed[1]) == 0.8
    assert pytest.approx(math.sqrt(sum(x * x for x in normed))) == 1.0

    # Zero vector safety
    zeros = [0.0, 0.0, 0.0]
    assert _l2_normalize(zeros) == [0.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_vector_engine_prefers_canonical_embedding_service_over_legacy_provider(
    tmp_path,
):
    identity = EmbeddingIdentity(provider="mock", model="canonical", dimension=4)
    service = EmbeddingService(identity=identity)
    legacy_provider = AsyncMock(side_effect=AssertionError("legacy provider used"))
    engine = VectorEngine(
        str(tmp_path / "vectors"),
        embedding_service=service,
        embedding_provider=legacy_provider,
    )
    engine._initialized = True

    vector = await engine.compute_embedding("canonical owner")

    assert len(vector) == 4
    legacy_provider.assert_not_called()
