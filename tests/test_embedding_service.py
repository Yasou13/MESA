"""Tests for Canonical EmbeddingService, Truthful Identity, and Egress Fence."""

import math
import pytest

from mesa_memory.embedding.service import (
    EmbeddingGenerationError,
    EmbeddingIdentity,
    EmbeddingIdentityMismatchError,
    EmbeddingService,
    EmbeddingUnavailableError,
    ExternalProviderForbiddenError,
    _l2_normalize,
)


def test_embedding_identity_space_id():
    ident = EmbeddingIdentity(
        provider="local",
        model="magibu/embeddingmagibu-200m",
        dimension=768,
        version="v1",
        normalized=True,
    )
    assert ident.embedding_space_id == "local:magibu/embeddingmagibu-200m:v1:768:norm=true"
    assert ident.dimension == 768
    assert ident.normalized is True


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

    with pytest.raises(EmbeddingUnavailableError, match="unavailable and no silent fallback"):
        service.embed_document("test text")

    with pytest.raises(EmbeddingUnavailableError, match="unavailable and no silent fallback"):
        service.embed_batch(["test 1", "test 2"])


def test_external_provider_egress_fence():
    """When external_enabled=False, external embedding providers are strictly forbidden."""
    ident = EmbeddingIdentity(
        provider="openai_compatible",
        model="text-embedding-3-small",
        dimension=1536,
    )

    # Should raise ExternalProviderForbiddenError immediately
    with pytest.raises(ExternalProviderForbiddenError, match="MESA_EXTERNAL_PROVIDER_ENABLED=false"):
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

    with pytest.raises(EmbeddingIdentityMismatchError, match="does not match configured identity dimension"):
        service.embed_document("sample text")


def test_l2_normalization_helper():
    raw = [3.0, 4.0]
    normed = _l2_normalize(raw)
    assert pytest.approx(normed[0]) == 0.6
    assert pytest.approx(normed[1]) == 0.8
    assert pytest.approx(math.sqrt(sum(x * x for x in normed))) == 1.0

    # Zero vector safety
    zeros = [0.0, 0.0, 0.0]
    assert _l2_normalize(zeros) == [0.0, 0.0, 0.0]
