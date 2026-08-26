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


class EmbeddingCompositionError(EmbeddingUnavailableError):
    """Configured embedding identity has no supported executable backend."""


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
                normalized=getattr(config, "embedding_normalized", True),
                model_revision=getattr(config, "embedding_model_revision", None),
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
        self._operational = False

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
            self._operational = True
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
                    options: dict[str, Any] = {"local_files_only": True}
                    if self._identity.model_revision is not None:
                        options["revision"] = self._identity.model_revision
                    self._local_model = SentenceTransformer(
                        self._identity.model, **options
                    )
                    self._operational = True
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
                self._operational = False

    def identity(self) -> EmbeddingIdentity:
        """Return the truthful identity of this embedding service."""
        return self._identity

    @property
    def identity_spec(self) -> EmbeddingIdentity:
        return self._identity

    @property
    def dimension(self) -> int:
        return self._identity.dimension

    @property
    def is_configured(self) -> bool:
        """Return whether an embedding identity/backend was configured."""
        return bool(self._identity.provider.strip())

    @property
    def is_operational(self) -> bool:
        """Return whether this service is operationally ready to generate embeddings."""
        return self._operational

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
            self._operational = True
            return [
                _deterministic_mock_vector(
                    t, self._identity.dimension, self._identity.normalized
                )
                for t in texts
            ]

        if self._provider_fn is not None:
            try:
                result = [
                    self._validate_vector_shape(self._provider_fn(t)) for t in texts
                ]
            except EmbeddingError:
                self._operational = False
                raise
            except Exception as exc:
                self._operational = False
                raise EmbeddingGenerationError(
                    f"Custom provider batch failed: {exc}"
                ) from exc
            self._operational = True
            return result

        if self._local_model is not None:
            try:
                embeddings = self._local_model.encode(
                    texts,
                    normalize_embeddings=self._identity.normalized,
                    show_progress_bar=False,
                )
                result = [
                    self._validate_vector_shape(
                        e.tolist() if hasattr(e, "tolist") else e
                    )
                    for e in embeddings
                ]
                self._operational = True
                return result
            except Exception as exc:
                self._operational = False
                raise EmbeddingGenerationError(
                    f"Batch embedding generation failed on local model '{self._identity.model}': {exc}"
                ) from exc

        self._operational = False
        raise EmbeddingUnavailableError(
            f"Embedding model '{self._identity.model}' ({self._identity.provider}) "
            "is unavailable and no silent fallback is permitted (fail-closed)."
        )

    def _embed_single_sync(self, text: str) -> list[float]:
        """Compute single text embedding synchronously."""
        if self._is_mock:
            self._operational = True
            return _deterministic_mock_vector(
                text, self._identity.dimension, self._identity.normalized
            )

        if self._provider_fn is not None:
            try:
                raw = self._provider_fn(text)
            except EmbeddingError:
                self._operational = False
                raise
            except Exception as exc:
                self._operational = False
                raise EmbeddingGenerationError(
                    f"Custom provider function failed: {exc}"
                ) from exc
            result = self._validate_vector_shape(raw)
            self._operational = True
            return result

        if self._local_model is not None:
            try:
                vec = self._local_model.encode(
                    text,
                    normalize_embeddings=self._identity.normalized,
                    show_progress_bar=False,
                )
                raw_list = vec.tolist() if hasattr(vec, "tolist") else list(vec)
                result = self._validate_vector_shape(raw_list)
                self._operational = True
                return result
            except Exception as exc:
                self._operational = False
                raise EmbeddingGenerationError(
                    f"Embedding generation failed on local model '{self._identity.model}': {exc}"
                ) from exc

        self._operational = False
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
            try:
                raw = await self._async_provider_fn(text)
                result = self._validate_vector_shape(raw)
            except EmbeddingError:
                self._operational = False
                raise
            except Exception as exc:
                self._operational = False
                raise EmbeddingGenerationError(
                    f"Custom async provider function failed: {exc}"
                ) from exc
            self._operational = True
            return result

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
            try:
                raw_list = await asyncio.gather(
                    *(self._async_provider_fn(t) for t in texts)
                )
                result = [self._validate_vector_shape(r) for r in raw_list]
            except EmbeddingError:
                self._operational = False
                raise
            except Exception as exc:
                self._operational = False
                raise EmbeddingGenerationError(
                    f"Custom async provider batch failed: {exc}"
                ) from exc
            self._operational = True
            return result

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(self.embed_batch, texts)
        )


class _OpenAICompatibleEmbeddingBackend:
    """Narrow OpenAI-compatible embedding transport, separate from LLM adapters."""

    def __init__(self, identity: EmbeddingIdentity) -> None:
        try:
            import openai
        except ImportError as exc:
            raise EmbeddingCompositionError(
                "OpenAI-compatible embedding support is not installed"
            ) from exc
        api_key = getattr(config, "llm_api_key", None) or getattr(
            config, "openai_api_key", None
        )
        if not api_key:
            raise EmbeddingCompositionError(
                "OpenAI-compatible embedding configuration requires LLM_API_KEY"
            )
        options = {
            "api_key": api_key,
            "base_url": getattr(config, "llm_base_url", None),
            "timeout": float(getattr(config, "llm_timeout_seconds", 20.0)),
            "max_retries": 0,
        }
        self._identity = identity
        self._sync = openai.OpenAI(**options)
        self._async = openai.AsyncOpenAI(**options)

    def embed(self, text: str) -> list[float]:
        try:
            response = self._sync.embeddings.create(
                model=self._identity.model, input=text
            )
            return list(response.data[0].embedding)
        except Exception as exc:
            raise EmbeddingGenerationError(
                f"OpenAI-compatible embedding request failed: {exc}"
            ) from exc

    async def aembed(self, text: str) -> list[float]:
        try:
            response = await self._async.embeddings.create(
                model=self._identity.model, input=text
            )
            return list(response.data[0].embedding)
        except Exception as exc:
            raise EmbeddingGenerationError(
                f"OpenAI-compatible embedding request failed: {exc}"
            ) from exc


def _compose_external_backend(
    identity: EmbeddingIdentity,
) -> tuple[Callable[[str], list[float]], Callable[[str], Any]]:
    """Compose a real, explicit external embedding transport at startup."""
    if identity.provider.lower() not in {"openai", "openai_compatible"}:
        raise EmbeddingCompositionError(
            f"External embedding provider '{identity.provider}' is unsupported"
        )
    backend = _OpenAICompatibleEmbeddingBackend(identity)
    return backend.embed, backend.aembed


# Global singleton holder
_GLOBAL_EMBEDDING_SERVICE: EmbeddingService | None = None


def get_embedding_service(
    *,
    identity: EmbeddingIdentity | None = None,
    allow_model_loading: bool = True,
    force_refresh: bool = False,
    external_enabled: bool | None = None,
    external_backend_factory: (
        Callable[
            [EmbeddingIdentity],
            tuple[Callable[[str], list[float]], Callable[[str], Any]],
        ]
        | None
    ) = None,
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
                model_revision=configured.model_revision,
            )
        effective_external_enabled = (
            getattr(config, "external_provider_enabled", False)
            if external_enabled is None
            else external_enabled
        )
        provider_fn: Callable[[str], list[float]] | None = None
        async_provider_fn: Callable[[str], Any] | None = None
        if identity.provider.lower() in {"openai", "openai_compatible"}:
            if not effective_external_enabled:
                raise ExternalProviderForbiddenError(
                    "External embedding provider is forbidden when "
                    "MESA_EXTERNAL_PROVIDER_ENABLED=false."
                )
            provider_fn, async_provider_fn = (
                external_backend_factory or _compose_external_backend
            )(identity)
        _GLOBAL_EMBEDDING_SERVICE = EmbeddingService(
            identity=identity,
            provider_fn=provider_fn,
            async_provider_fn=async_provider_fn,
            allow_model_loading=allow_model_loading,
            external_enabled=effective_external_enabled,
        )
    return _GLOBAL_EMBEDDING_SERVICE


def set_global_embedding_service(service: EmbeddingService | None) -> None:
    """Set or reset the global embedding service instance."""
    global _GLOBAL_EMBEDDING_SERVICE
    _GLOBAL_EMBEDDING_SERVICE = service
