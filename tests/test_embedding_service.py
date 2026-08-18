"""Tests for Canonical EmbeddingService, Truthful Identity, and Egress Fence."""

import math
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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
from mesa_storage.vector_engine import VectorEngine


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

        def embed(self, _text):
            return [0.25] * 4

        async def aembed(self, _text):
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
