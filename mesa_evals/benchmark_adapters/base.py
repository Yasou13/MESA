# MESA v0.6.0 — BaseMemoryClient ABC
# Defines the contract all benchmark memory clients must fulfil.
# Decoupled from any specific storage backend to enable clean A/B
# comparison between MESA's full pipeline and dumb baselines.
"""
Abstract base class for benchmark memory clients.

Every implementation must provide three core operations:

1. ``add_memory(content, agent_id)`` — Ingest text into the memory space.
2. ``query(question, agent_id)`` — Retrieve the most relevant context.
3. ``clear_memory(agent_id)`` — Purge all state for strict scenario isolation.

All operations are **async-first** and carry a mandatory ``agent_id`` parameter
to enforce namespace isolation across benchmark scenarios.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("MESA_BenchmarkClient")


# ---------------------------------------------------------------------------
# Standardised query result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryResult:
    """Immutable container for a memory query response.

    Attributes:
        context: The retrieved text context (may be multi-chunk concatenation).
        chunks: Individual retrieved chunks with metadata.
        total_chunks: Number of chunks in the memory space that were searched.
        error: Non-None if the query failed gracefully.
    """

    context: str
    chunks: list[dict[str, Any]] = field(default_factory=list)
    total_chunks: int = 0
    error: str | None = None

    # Sentinel for graceful degradation
    NO_CONTEXT = "No relevant context found."

    @classmethod
    def empty(cls) -> "QueryResult":
        """Return a standardised empty result for graceful degradation."""
        return cls(
            context=cls.NO_CONTEXT,
            chunks=[],
            total_chunks=0,
            error=None,
        )

    @classmethod
    def from_error(cls, error: str) -> "QueryResult":
        """Return a standardised error result."""
        return cls(
            context=cls.NO_CONTEXT,
            chunks=[],
            total_chunks=0,
            error=error,
        )


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseMemoryClient(ABC):
    """Abstract base class for all benchmark memory clients.

    Enforces a strict three-method contract:
        - ``add_memory``: Ingest a single document/chunk.
        - ``query``: Retrieve relevant context for a question.
        - ``clear_memory``: Purge all data for a given agent.

    Every method requires ``agent_id`` as a mandatory parameter.
    Implementations MUST NOT cache or share state across agent IDs.

    Lifecycle::

        client = SomeClient(...)
        await client.initialize()

        # Per-scenario loop:
        await client.clear_memory(agent_id="benchmark_CONF_001")
        await client.add_memory("...", agent_id="benchmark_CONF_001")
        result = await client.query("...", agent_id="benchmark_CONF_001")

        await client.shutdown()
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Initialise backend connections and resources.

        Subclasses should override this to set up storage engines,
        embedding models, etc.  Default implementation is a no-op.
        """

    async def shutdown(self) -> None:
        """Release backend connections and resources.

        Subclasses should override this to cleanly close storage
        handles.  Default implementation is a no-op.
        """

    # ------------------------------------------------------------------
    # Core contract
    # ------------------------------------------------------------------

    @abstractmethod
    async def add_memory(
        self,
        content: str,
        *,
        agent_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Ingest a text document into the memory space.

        Args:
            content: Raw text to store and index.
            agent_id: **Mandatory** namespace isolation key.
            metadata: Optional key-value metadata attached to the record.

        Returns:
            A unique record identifier (e.g., UUID).

        Raises:
            ValueError: If ``agent_id`` is empty or invalid.
        """
        ...

    @abstractmethod
    async def query(
        self,
        question: str,
        *,
        agent_id: str,
        limit: int = 5,
    ) -> QueryResult:
        """Retrieve the most relevant context for a question.

        Must NEVER raise an unhandled exception.  On failure, return
        ``QueryResult.from_error(...)`` with a descriptive message.

        Args:
            question: Natural-language question or search query.
            agent_id: **Mandatory** namespace isolation key.
            limit: Maximum number of chunks to retrieve.

        Returns:
            A ``QueryResult`` containing the retrieved context.
        """
        ...

    @abstractmethod
    async def clear_memory(self, *, agent_id: str) -> int:
        """Purge ALL data for the given agent_id.

        This must guarantee a completely pristine state — no residual
        vectors, nodes, or cached embeddings may survive.

        Args:
            agent_id: **Mandatory** namespace isolation key.

        Returns:
            Number of records purged.

        Raises:
            ValueError: If ``agent_id`` is empty or invalid.
        """
        ...

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_agent_id(agent_id: str) -> None:
        """Reject structurally invalid agent_id values.

        Raises:
            ValueError: If agent_id is empty, None, or whitespace-only.
        """
        if not agent_id or not agent_id.strip():
            raise ValueError(f"agent_id must be a non-empty string. Got: {agent_id!r}")

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>"
