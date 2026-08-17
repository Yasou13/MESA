"""Canonical Embedding Service for MESA V4.

Owns:
1. Document and query embedding generation.
2. Embedding-space identity management (truthful identity, no silent cross-family fallback).
3. Fail-closed error semantics on provider / model unavailability.
4. Hard external provider egress fence enforcement.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from mesa_memory.config import config, configured_embedding_identity

logger = logging.getLogger("MESA_EmbeddingService")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class EmbeddingError(RuntimeError):
    """Base exception for all embedding service failures."""


class EmbeddingGenerationError(EmbeddingError):
    """Failed to compute vector embedding."""


class EmbeddingUnavailableError(EmbeddingError):
    """The requested embedding provider or model is unavailable (fail-closed)."""


class ExternalProviderForbiddenError(EmbeddingError):
    """External provider egress is blocked by MESA_EXTERNAL_PROVIDER_ENABLED=false."""


class EmbeddingIdentityMismatchError(EmbeddingError):
    """Vector dimension or space does not match the configured embedding identity."""


# ---------------------------------------------------------------------------
# Truthful Embedding Identity
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EmbeddingIdentity:
    """Truthful, immutable description of the embedding space."""

    provider: str
    model: str
    dimension: int
    version: str = "v1"
    normalized: bool = True
    model_revision: str | None = None
    embedding_space_id: str = field(init=False)

    def __post_init__(self) -> None:
        space_id = (
            f"{self.provider.strip()}:{self.model.strip()}:"
            f"{(self.model_revision or self.version).strip()}:{self.dimension}:"
            f"norm={str(self.normalized).lower()}"
        )
        object.__setattr__(self, "embedding_space_id", space_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "embedding_space_id": self.embedding_space_id,
            "provider": self.provider,
            "model": self.model,
            "dimension": self.dimension,
            "version": self.version,
            "normalized": self.normalized,
            "model_revision": self.model_revision,
        }


def _l2_normalize(vec: Sequence[float]) -> list[float]:
    """Return L2-normalized float list."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm < 1e-12:
        return [0.0] * len(vec)
    return [float(x / norm) for x in vec]


def _deterministic_mock_vector(
    text: str, dimension: int, normalized: bool = True
) -> list[float]:
    """Generate a deterministic pseudo-embedding for testing."""
    h = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [((h[i % len(h)] + i * 31) % 256) / 255.0 - 0.5 for i in range(dimension)]
    if normalized:
        return _l2_normalize(raw)
    return raw


# ---------------------------------------------------------------------------
# Canonical EmbeddingService
# ---------------------------------------------------------------------------
class EmbeddingService:
    """Canonical owner of embedding generation and embedding-space identity."""

    def __init__(
        self,
        *,
        identity: EmbeddingIdentity | None = None,
        provider_fn: Callable[[str], list[float]] | None = None,
        async_provider_fn: Callable[[str], Any] | None = None,
        allow_model_loading: bool = True,
        external_enabled: bool | None = None,
    ) -> None:
        if identity is not None:
            self._identity = identity
        else:
            # Build default Round 5 identity
            self._identity = EmbeddingIdentity(
                provider="local",
                model=getattr(
                    config, "local_embedding_model", "magibu/embeddingmagibu-200m"
                ),
                dimension=getattr(config, "embedding_dimension", 768),
                version=getattr(config, "embedding_version", "v1"),
                normalized=True,
            )

        self._external_enabled = (
            external_enabled
            if external_enabled is not None
            else getattr(config, "external_provider_enabled", False)
        )
        self._allow_model_loading = allow_model_loading
        self._provider_fn = provider_fn
        self._async_provider_fn = async_provider_fn

        self._local_model: Any = None
        self._is_mock = False

        # Validate external provider egress fence
        self._validate_egress_fence()

        # Initialize backend if needed
        self._init_backend()

    def _validate_egress_fence(self) -> None:
        """Enforce external provider egress fence."""
        external_providers = {
            "openai",
            "openai_compatible",
            "claude",
            "anthropic",
            "hosted",
        }
        if (
            not self._external_enabled
            and self._identity.provider.lower() in external_providers
        ):
            raise ExternalProviderForbiddenError(
                f"External embedding provider '{self._identity.provider}' is forbidden "
                "when MESA_EXTERNAL_PROVIDER_ENABLED=false."
            )

    def _init_backend(self) -> None:
        """Initialize the local or mock model backend."""
        if self._provider_fn is not None or self._async_provider_fn is not None:
            return

        provider = self._identity.provider.lower()

        if provider in ("mock", "deterministic", "test"):
            self._is_mock = True
            return

        if provider in ("local", "sentence-transformers", "sentence_transformers"):
            if not self._allow_model_loading:
                logger.info(
                    "Local embedding model loading is disabled by configuration."
                )
                return

            try:
                from sentence_transformers import SentenceTransformer

                # Model acquisition is explicit operator work; canonical
                # runtime loading must never reach out to download a model.
                try:
                    self._local_model = SentenceTransformer(
                        self._identity.model, local_files_only=True
                    )
                except Exception as exc:
                    logger.info(
                        "Local embedding model %s is unavailable in the local cache: %s",
                        self._identity.model,
                        exc,
                    )
            except Exception as exc:
                logger.warning(
                    "EMBEDDING_BACKEND_INIT_FAILED | model=%s provider=%s error=%s",
                    self._identity.model,
                    self._identity.provider,
                    exc,
                )
                # Do NOT silently switch to another model!
                self._local_model = None

    def identity(self) -> EmbeddingIdentity:
        """Return the truthful identity of this embedding service."""
        return self._identity

    @property
    def identity_spec(self) -> EmbeddingIdentity:
        return self._identity

    @property
    def dimension(self) -> int:
        return self._identity.dimension

    def _validate_vector_shape(self, vec: Sequence[float]) -> list[float]:
        """Ensure vector conforms to identity dimension and normalization."""
        if len(vec) != self._identity.dimension:
            raise EmbeddingIdentityMismatchError(
                f"Generated vector dimension {len(vec)} does not match "
                f"configured identity dimension {self._identity.dimension}"
            )
        result = [float(x) for x in vec]
        if self._identity.normalized:
            result = _l2_normalize(result)
        return result

    # -----------------------------------------------------------------------
    # Synchronous API
    # -----------------------------------------------------------------------
    def embed_document(self, text: str) -> list[float]:
        """Embed a document synchronously."""
        return self._embed_single_sync(text)

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query synchronously."""
        return self._embed_single_sync(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts synchronously."""
        if not texts:
            return []

        if self._is_mock:
            return [
                _deterministic_mock_vector(
                    t, self._identity.dimension, self._identity.normalized
                )
                for t in texts
            ]

        if self._provider_fn is not None:
            return [self._validate_vector_shape(self._provider_fn(t)) for t in texts]

        if self._local_model is not None:
            try:
                embeddings = self._local_model.encode(
                    texts,
                    normalize_embeddings=self._identity.normalized,
                    show_progress_bar=False,
                )
                return [
                    self._validate_vector_shape(
                        e.tolist() if hasattr(e, "tolist") else e
                    )
                    for e in embeddings
                ]
            except Exception as exc:
                raise EmbeddingGenerationError(
                    f"Batch embedding generation failed on local model '{self._identity.model}': {exc}"
                ) from exc

        raise EmbeddingUnavailableError(
            f"Embedding model '{self._identity.model}' ({self._identity.provider}) "
            "is unavailable and no silent fallback is permitted (fail-closed)."
        )

    def _embed_single_sync(self, text: str) -> list[float]:
        """Compute single text embedding synchronously."""
        if self._is_mock:
            return _deterministic_mock_vector(
                text, self._identity.dimension, self._identity.normalized
            )

        if self._provider_fn is not None:
            try:
                raw = self._provider_fn(text)
            except EmbeddingError:
                raise
            except Exception as exc:
                raise EmbeddingGenerationError(
                    f"Custom provider function failed: {exc}"
                ) from exc
            return self._validate_vector_shape(raw)

        if self._local_model is not None:
            try:
                vec = self._local_model.encode(
                    text,
                    normalize_embeddings=self._identity.normalized,
                    show_progress_bar=False,
                )
                raw_list = vec.tolist() if hasattr(vec, "tolist") else list(vec)
                return self._validate_vector_shape(raw_list)
            except Exception as exc:
                raise EmbeddingGenerationError(
                    f"Embedding generation failed on local model '{self._identity.model}': {exc}"
                ) from exc

        raise EmbeddingUnavailableError(
            f"Embedding model '{self._identity.model}' ({self._identity.provider}) "
            "is unavailable and no silent fallback is permitted (fail-closed)."
        )

    # -----------------------------------------------------------------------
    # Asynchronous API
    # -----------------------------------------------------------------------
    async def aembed_document(self, text: str) -> list[float]:
        """Embed a document asynchronously."""
        if self._async_provider_fn is not None:
            raw = await self._async_provider_fn(text)
            return self._validate_vector_shape(raw)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(self._embed_single_sync, text)
        )

    async def aembed_query(self, text: str) -> list[float]:
        """Embed a search query asynchronously."""
        return await self.aembed_document(text)

    async def aembed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts asynchronously."""
        if not texts:
            return []

        if self._async_provider_fn is not None:
            raw_list = await asyncio.gather(
                *(self._async_provider_fn(t) for t in texts)
            )
            return [self._validate_vector_shape(r) for r in raw_list]

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(self.embed_batch, texts)
        )


# Global singleton holder
_GLOBAL_EMBEDDING_SERVICE: EmbeddingService | None = None


def get_embedding_service(
    *,
    identity: EmbeddingIdentity | None = None,
    allow_model_loading: bool = True,
    force_refresh: bool = False,
) -> EmbeddingService:
    """Factory and dependency injector for EmbeddingService."""
    global _GLOBAL_EMBEDDING_SERVICE
    if _GLOBAL_EMBEDDING_SERVICE is None or force_refresh:
        if identity is None:
            configured = configured_embedding_identity()
            identity = EmbeddingIdentity(
                provider=configured.provider,
                model=configured.model,
                dimension=configured.dimension,
                version=configured.version,
                normalized=configured.normalized,
            )
        _GLOBAL_EMBEDDING_SERVICE = EmbeddingService(
            identity=identity,
            allow_model_loading=allow_model_loading,
        )
    return _GLOBAL_EMBEDDING_SERVICE


def set_global_embedding_service(service: EmbeddingService | None) -> None:
    """Set or reset the global embedding service instance."""
    global _GLOBAL_EMBEDDING_SERVICE
    _GLOBAL_EMBEDDING_SERVICE = service
