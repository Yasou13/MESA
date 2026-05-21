# MESA v0.3.0 — Phase 2: Bi-Temporal Context Retriever
# Read-path service that queries the DAO layer and applies bi-temporal gating:
#   - Consolidated records (is_consolidated=1) are emitted as-is.
#   - Unconsolidated records (is_consolidated=0) are wrapped in a strict
#     Markdown system warning before injection into the LLM context window.
#
# Design:
#   - All queries pass through MemoryDAO, inheriting mandatory agent_id
#     RLS enforcement from the DAO layer — no direct SQL here.
#   - The warning wrapper is injected at the record level, NOT the batch
#     level, to give the LLM per-record epistemic awareness.
#   - Token budgeting via an optional embedder interface prevents context
#     overflow on large memory stores.
"""
Bi-temporal context augmentation retriever for the MESA read path.

Queries the :class:`~mesa_storage.dao.MemoryDAO` to fetch both consolidated
and unconsolidated memory records, applies per-record epistemic gating
(Markdown warning injection for unverified data), and returns a fully
formatted context string ready for LLM consumption.

Gating Rules:
    1. **Both** ``is_consolidated = TRUE`` and ``is_consolidated = FALSE``
       records are retrieved — nothing is silently dropped.
    2. Any record with ``is_consolidated = FALSE`` receives a strict
       system warning wrapper alerting the downstream LLM that the data
       has not yet been validated by the background REM consolidation cycle.

Usage::

    from mesa_memory.retriever import ContextRetriever

    retriever = ContextRetriever(dao=dao)
    context = await retriever.retrieve_context(
        agent_id="agent_alpha",
        query_vector=[0.1, 0.2, ...],
    )
    # context is a Markdown string ready for LLM prompt injection.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from mesa_storage.dao import MemoryDAO

logger = logging.getLogger("MESA_Retriever")

# ---------------------------------------------------------------------------
# Bi-temporal gating — unconsolidated record warning
# ---------------------------------------------------------------------------

# Strict Markdown wrapper injected around EVERY unconsolidated record.
# Turkish-language system warning per MESA specification.
_UNCONSOLIDATED_WARNING = (
    "[SİSTEM UYARISI: Aşağıdaki veri son zamanlarda eklenmiştir "
    "ve arka plan REM döngüsü tarafından henüz doğrulanmamıştır. "
    "Çelişkilere karşı dikkatli ol.]"
)


# ---------------------------------------------------------------------------
# Optional token counting interface
# ---------------------------------------------------------------------------


@runtime_checkable
class TokenCounter(Protocol):
    """Minimal interface for token budget enforcement."""

    def get_token_count(self, text: str) -> int:
        ...


class _NoOpCounter:
    """Fallback counter — always returns character length as proxy."""

    def get_token_count(self, text: str) -> int:
        # Rough heuristic: 1 token ≈ 4 characters (English average).
        # Conservative to avoid overflow in safety-critical paths.
        return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Context Retriever — bi-temporal read path
# ---------------------------------------------------------------------------


class ContextRetriever:
    """Bi-temporal context augmentation service for the MESA read path.

    Fetches memory records from the DAO (which enforces agent_id RLS),
    applies per-record epistemic gating for unconsolidated data, and
    returns a fully formatted Markdown context string.

    Args:
        dao: A :class:`~mesa_storage.dao.MemoryDAO` instance.
        token_counter: Optional token counting interface for budget
                       enforcement.  Falls back to a character-based
                       heuristic if not provided.
        max_context_tokens: Hard ceiling on output context tokens.
                            Defaults to 8000.
    """

    __slots__ = ("_dao", "_counter", "_max_tokens")

    def __init__(
        self,
        dao: MemoryDAO,
        *,
        token_counter: TokenCounter | None = None,
        max_context_tokens: int = 8000,
    ) -> None:
        self._dao = dao
        self._counter: TokenCounter = token_counter or _NoOpCounter()
        self._max_tokens = max_context_tokens

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def dao(self) -> MemoryDAO:
        """Return the underlying DAO (read-only access)."""
        return self._dao

    # ==================================================================
    # PRIMARY API — Vector-based context retrieval
    # ==================================================================

    async def retrieve_context(
        self,
        agent_id: str,
        *,
        query_vector: list[float],
        limit: int = 10,
        max_tokens: int | None = None,
    ) -> str:
        """Retrieve and format context for LLM consumption via vector search.

        Performs a cosine similarity search through the DAO (agent-scoped),
        enriches results with graph metadata (including ``is_consolidated``),
        and applies bi-temporal gating before assembling the final Markdown
        context string.

        Args:
            agent_id: **Mandatory** tenant isolation key (passed to DAO).
            query_vector: Float32 query embedding for similarity search.
            limit: Maximum records to retrieve.
            max_tokens: Override for the context token budget.

        Returns:
            A Markdown-formatted context string.  Unconsolidated records
            are wrapped in the system warning.  Returns
            ``"Retrieved Context: None"`` when no records match.
        """
        # Query DAO with include_graph=True to get is_consolidated flag
        results = await self._dao.search_memory(
            agent_id,
            query_vector=query_vector,
            limit=limit,
            include_graph=True,
        )

        if not results:
            return "Retrieved Context: None"

        return self._format_context(results, max_tokens=max_tokens)

    # ==================================================================
    # SECONDARY API — FTS5 lexical context retrieval
    # ==================================================================

    async def retrieve_context_fts(
        self,
        agent_id: str,
        *,
        query: str,
        limit: int = 20,
        max_tokens: int | None = None,
    ) -> str:
        """Retrieve and format context via FTS5 lexical search.

        Uses the DAO's FTS5 search (agent-scoped) for zero-VRAM lexical
        pre-filtering, then applies bi-temporal gating.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            query: FTS5 MATCH expression.
            limit: Maximum records.
            max_tokens: Override for the context token budget.

        Returns:
            Formatted Markdown context string with epistemic gating.
        """
        results = await self._dao.search_memory_fts(
            agent_id,
            query=query,
            limit=limit,
        )

        if not results:
            return "Retrieved Context: None"

        # FTS results come directly from nodes table — already have
        # is_consolidated field.  Wrap each in a vector-compatible dict.
        wrapped = []
        for r in results:
            wrapped.append(
                {
                    "node_id": r.get("id", ""),
                    "graph": r,
                }
            )

        return self._format_context(wrapped, max_tokens=max_tokens)

    # ==================================================================
    # TERTIARY API — Direct node retrieval (all memories)
    # ==================================================================

    async def retrieve_all_context(
        self,
        agent_id: str,
        *,
        limit: int | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Retrieve all active memories for an agent with bi-temporal gating.

        Fetches both consolidated and unconsolidated records via the DAO,
        applies per-record epistemic warnings, and formats for LLM use.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            limit: Optional record ceiling.
            max_tokens: Override for the context token budget.

        Returns:
            Formatted Markdown context string.
        """
        # include_consolidated=True ensures BOTH consolidated and
        # unconsolidated records are retrieved (Rule 1).
        records = await self._dao.get_memories(
            agent_id,
            limit=limit,
            include_consolidated=True,
        )

        if not records:
            return "Retrieved Context: None"

        # Wrap in the graph-enriched format expected by _format_context
        wrapped = [{"node_id": r.get("id", ""), "graph": r} for r in records]

        return self._format_context(wrapped, max_tokens=max_tokens)

    # ==================================================================
    # FORMATTING — bi-temporal gating and token budgeting
    # ==================================================================

    def _format_context(
        self,
        results: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
    ) -> str:
        """Apply bi-temporal gating and assemble the final context string.

        For each record:
            - If ``is_consolidated == True`` (or 1): emit content as-is.
            - If ``is_consolidated == False`` (or 0): wrap content in
              the ``_UNCONSOLIDATED_WARNING`` Markdown block.

        Token budgeting uses whole-node inclusion policy — a record is
        either fully included or entirely discarded.  No partial slicing.

        Args:
            results: List of search result dicts.  Each should have a
                     ``"graph"`` sub-dict with ``entity_name``,
                     ``is_consolidated``, ``type``, and ``created_at``.
            max_tokens: Token budget override.

        Returns:
            Formatted Markdown context string.
        """
        budget = max_tokens if max_tokens is not None else self._max_tokens
        header = "Retrieved Context:"
        remaining = budget - self._counter.get_token_count(header)

        if remaining <= 0:
            return "Retrieved Context: None"

        entries: list[str] = []

        for idx, result in enumerate(results):
            graph = result.get("graph", {})
            node_id = result.get("node_id", graph.get("id", "unknown"))
            entity_name = graph.get("entity_name", "")
            node_type = graph.get("type", "ENTITY")
            created_at = graph.get("created_at", "")
            is_consolidated = graph.get("is_consolidated", 0)

            # Build the raw content line
            raw_data = (
                f"Entity: {entity_name} | Type: {node_type} | "
                f"Created: {created_at} | NodeID: {node_id}"
            )

            # ---- BI-TEMPORAL GATE (Rule 2) ----------------------------
            # is_consolidated can be bool or int (0/1 from SQLite)
            if not is_consolidated:
                # UNCONSOLIDATED: inject strict Markdown warning wrapper
                entry = f"\n[{idx + 1}] {_UNCONSOLIDATED_WARNING}\nContent: {raw_data}"
            else:
                # CONSOLIDATED: emit as-is, no warning
                entry = f"\n[{idx + 1}] {raw_data}"

            # ---- Token budget gate (whole-node policy) ----------------
            entry_tokens = self._counter.get_token_count(entry)
            if entry_tokens > remaining:
                # Record does not fit — discard entirely, stop iteration.
                logger.debug(
                    "CONTEXT_BUDGET_EXCEEDED | idx=%d tokens_needed=%d "
                    "remaining=%d — truncating context",
                    idx,
                    entry_tokens,
                    remaining,
                )
                break

            entries.append(entry)
            remaining -= entry_tokens

        if not entries:
            return "Retrieved Context: None"

        context = header + "".join(entries)

        # Log summary for observability
        total = len(results)
        included = len(entries)
        unconsolidated_count = sum(1 for e in entries if _UNCONSOLIDATED_WARNING in e)
        logger.info(
            "CONTEXT_RETRIEVED | total=%d included=%d unconsolidated=%d tokens_used=%d",
            total,
            included,
            unconsolidated_count,
            budget - remaining,
        )

        return context

    # ==================================================================
    # UTILITY — raw record retrieval without formatting
    # ==================================================================

    async def retrieve_raw(
        self,
        agent_id: str,
        *,
        query_vector: list[float],
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return raw search results with bi-temporal metadata intact.

        Useful when the caller needs programmatic access to the records
        and ``is_consolidated`` flags without Markdown formatting.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            query_vector: Float32 query embedding.
            limit: Maximum results.

        Returns:
            List of result dicts with ``graph`` sub-dict containing
            ``is_consolidated`` and other node metadata.
        """
        return await self._dao.search_memory(
            agent_id,
            query_vector=query_vector,
            limit=limit,
            include_graph=True,
        )
