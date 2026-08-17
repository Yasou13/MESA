"""MESA Canonical Embedding Subsystem."""

from mesa_memory.embedding.service import (
    EmbeddingError,
    EmbeddingGenerationError,
    EmbeddingIdentity,
    EmbeddingService,
    EmbeddingUnavailableError,
    ExternalProviderForbiddenError,
    get_embedding_service,
)

__all__ = [
    "EmbeddingError",
    "EmbeddingGenerationError",
    "EmbeddingIdentity",
    "EmbeddingService",
    "EmbeddingUnavailableError",
    "ExternalProviderForbiddenError",
    "get_embedding_service",
]
