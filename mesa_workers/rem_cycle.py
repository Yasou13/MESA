# MESA v0.3.0 — Phase 2: Asynchronous REM (Consolidation) Cycle Worker
# Background asyncio job that polls for unconsolidated memory records and
# drives them through Dual-LLM consensus conflict resolution.
#
# Architecture:
#   - Polls the DAO for `is_consolidated = FALSE` records per agent.
#   - Fires the consensus pipeline ONLY when the unconsolidated queue
#     exceeds the configurable activation threshold (default 50).
#   - Hard token/record budget per cycle prevents infinite LLM API cost
#     scaling during traffic spikes — large queues are processed in FIFO
#     batches across successive cycles.
#   - Conflict resolution: contradictory data invalidates the OLD node
#     (UPDATE invalid_at), promotes the NEW node (is_consolidated=1),
#     and links it to the graph.  NO physical DELETEs.
#   - Follows the MaintenanceWorker lifecycle pattern: start/stop/run_now.
"""
Asynchronous REM (Rapid Eye Movement) consolidation cycle worker.

Runs as a background ``asyncio`` task that continuously polls the
:class:`~mesa_storage.dao.MemoryDAO` for unconsolidated memory records.
When the queue depth exceeds the activation threshold, the worker
processes records through a Dual-LLM consensus pipeline to detect
contradictions against existing consolidated knowledge.

**Conflict Resolution Protocol:**

- **No contradiction:** The new node is simply marked
  ``is_consolidated = TRUE``.
- **Contradiction detected:** The OLD consolidated node is invalidated
  via ``UPDATE SET invalid_at = CURRENT_TIMESTAMP`` (never physically
  deleted).  The NEW node is promoted to ``is_consolidated = TRUE`` and
  linked into the knowledge graph.

**Token Budget Limiter:**

A hard per-cycle cap on records processed prevents runaway LLM API
costs during ingestion spikes.  If the unconsolidated queue exceeds
the cycle budget, remaining records are deferred to subsequent cycles
in strict FIFO order.

Usage::

    from mesa_workers.rem_cycle import REMCycleWorker

    worker = REMCycleWorker(
        dao=dao,
        llm_a=llm_primary,
        llm_b=llm_secondary,
    )
    task = asyncio.create_task(worker.start())
    ...
    await worker.stop()

Or as an async context manager::

    async with REMCycleWorker(dao, llm_a, llm_b) as worker:
        await app_main_loop()
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_storage.dao import MemoryDAO

logger = logging.getLogger("MESA_REM")

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

# Minimum unconsolidated records before the consensus pipeline activates.
# Below this threshold, the worker sleeps — prevents noisy LLM calls on
# low-volume streams.
_DEFAULT_ACTIVATION_THRESHOLD = 50

# Maximum records processed per cycle — hard budget to prevent infinite
# LLM API cost scaling during traffic spikes.
_DEFAULT_MAX_RECORDS_PER_CYCLE = 100

# Poll interval in seconds between queue depth checks.
_DEFAULT_POLL_INTERVAL_SECONDS = 30

# Grace period before first poll to let the application fully start.
_STARTUP_GRACE_SECONDS = 15


# ---------------------------------------------------------------------------
# Conflict resolution prompt templates
# ---------------------------------------------------------------------------

_CONTRADICTION_PROMPT_A = """\
Role: You are a knowledge graph consistency analyst.
Task: Determine if the NEW memory record contradicts the EXISTING consolidated
knowledge below.  A contradiction exists when the new data asserts facts that
are mutually exclusive with, or semantically incompatible with, the existing
record.

EXISTING consolidated record:
  Entity: {existing_entity}
  Type: {existing_type}
  Created: {existing_created}

NEW unconsolidated record:
  Entity: {new_entity}
  Type: {new_type}
  Created: {new_created}

Respond ONLY with valid JSON:
{{"contradiction": true or false, "justification": "..."}}"""

_CONTRADICTION_PROMPT_B = """\
Role: You are an independent fact-checker with no prior context.
Task: Objectively assess whether the NEW record below logically contradicts
the EXISTING record.  Focus on factual incompatibility, not mere difference
in wording.

EXISTING consolidated record:
  Entity: {existing_entity}
  Type: {existing_type}

NEW unconsolidated record:
  Entity: {new_entity}
  Type: {new_type}

Respond ONLY with valid JSON:
{{"contradiction": true or false, "justification": "..."}}"""


# ---------------------------------------------------------------------------
# REM cycle metrics
# ---------------------------------------------------------------------------


@dataclass
class REMCycleMetrics:
    """Tracks REM consolidation cycle statistics for observability."""

    cycles_completed: int = 0
    cycles_skipped: int = 0
    cycles_failed: int = 0
    records_consolidated: int = 0
    records_contradicted: int = 0
    records_promoted: int = 0
    total_cycle_time_ms: float = 0.0
    last_cycle_at: str | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def record_cycle(
        self,
        consolidated: int,
        contradicted: int,
        promoted: int,
        duration_ms: float,
    ) -> None:
        async with self._lock:
            self.cycles_completed += 1
            self.records_consolidated += consolidated
            self.records_contradicted += contradicted
            self.records_promoted += promoted
            self.total_cycle_time_ms += duration_ms
            self.last_cycle_at = datetime.now(timezone.utc).isoformat()

    async def record_skip(self) -> None:
        async with self._lock:
            self.cycles_skipped += 1

    async def record_failure(self) -> None:
        async with self._lock:
            self.cycles_failed += 1

    def snapshot(self) -> dict:
        return {
            "cycles_completed": self.cycles_completed,
            "cycles_skipped": self.cycles_skipped,
            "cycles_failed": self.cycles_failed,
            "records_consolidated": self.records_consolidated,
            "records_contradicted": self.records_contradicted,
            "records_promoted": self.records_promoted,
            "total_cycle_time_ms": round(self.total_cycle_time_ms, 2),
            "last_cycle_at": self.last_cycle_at,
        }


# ---------------------------------------------------------------------------
# Conflict Resolution — Dual-LLM consensus
# ---------------------------------------------------------------------------


def _parse_contradiction_response(raw: Any, llm_label: str) -> bool:
    """Parse a contradiction boolean from raw LLM output.

    Returns ``True`` if the LLM detected a contradiction.
    Defaults to ``False`` (no contradiction) on parse failure to avoid
    false-positive invalidation of consolidated data.
    """
    try:
        text = raw if isinstance(raw, str) else str(raw)
        # Strip markdown code fences if present
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(
                line for line in lines if not line.strip().startswith("```")
            )
        result = json.loads(text)
        contradiction = result.get("contradiction", False)
        # Handle string "true"/"false" from some LLMs
        if isinstance(contradiction, str):
            return contradiction.lower() == "true"
        return bool(contradiction)
    except (json.JSONDecodeError, TypeError, AttributeError) as exc:
        logger.warning(
            "CONTRADICTION_PARSE_ERROR | llm=%s error=%s — defaulting to no-contradiction",
            llm_label,
            exc,
        )
        return False


async def evaluate_contradiction(
    llm_a: BaseUniversalLLMAdapter,
    llm_b: BaseUniversalLLMAdapter,
    existing_node: dict[str, Any],
    new_node: dict[str, Any],
) -> bool:
    """Run Dual-LLM consensus to detect contradiction between nodes.

    Decision matrix:
        - Both agree contradiction → ``True``
        - Both agree no contradiction → ``False``
        - Disagree → ``False`` (fail-safe: preserve existing data)

    Args:
        llm_a: Primary LLM adapter.
        llm_b: Secondary LLM adapter (independent evaluator).
        existing_node: Consolidated node dict from the graph.
        new_node: Unconsolidated node dict pending consolidation.

    Returns:
        ``True`` if dual consensus confirms a contradiction.
    """
    prompt_a = _CONTRADICTION_PROMPT_A.format(
        existing_entity=existing_node.get("entity_name", ""),
        existing_type=existing_node.get("type", "ENTITY"),
        existing_created=existing_node.get("created_at", ""),
        new_entity=new_node.get("entity_name", ""),
        new_type=new_node.get("type", "ENTITY"),
        new_created=new_node.get("created_at", ""),
    )
    prompt_b = _CONTRADICTION_PROMPT_B.format(
        existing_entity=existing_node.get("entity_name", ""),
        existing_type=existing_node.get("type", "ENTITY"),
        new_entity=new_node.get("entity_name", ""),
        new_type=new_node.get("type", "ENTITY"),
    )

    # Run both LLMs concurrently
    raw_a, raw_b = await asyncio.gather(
        llm_a.acomplete(prompt_a),
        llm_b.acomplete(prompt_b),
    )

    contradiction_a = _parse_contradiction_response(raw_a, "LLM_A")
    contradiction_b = _parse_contradiction_response(raw_b, "LLM_B")

    if contradiction_a and contradiction_b:
        logger.info(
            "CONTRADICTION_CONFIRMED | existing=%s new=%s",
            existing_node.get("id", "?"),
            new_node.get("id", "?"),
        )
        return True

    if contradiction_a != contradiction_b:
        logger.info(
            "CONTRADICTION_DISAGREEMENT | A=%s B=%s existing=%s new=%s "
            "— fail-safe: no contradiction",
            contradiction_a,
            contradiction_b,
            existing_node.get("id", "?"),
            new_node.get("id", "?"),
        )

    return False


# ---------------------------------------------------------------------------
# Conflict Resolution — Graph operations
# ---------------------------------------------------------------------------


async def resolve_conflict(
    dao: MemoryDAO,
    agent_id: str,
    existing_node: dict[str, Any],
    new_node: dict[str, Any],
) -> None:
    """Execute the conflict resolution protocol for a confirmed contradiction.

    Protocol:
        1. Invalidate the OLD consolidated node via
           ``UPDATE SET invalid_at = CURRENT_TIMESTAMP``.
           The old node is NEVER physically deleted.
        2. Promote the NEW node to ``is_consolidated = TRUE``.
        3. Link the new node into the graph via an edge recording
           the supersession relationship.

    All operations go through the DAO, inheriting agent_id RLS.

    Args:
        dao: MemoryDAO instance (enforces agent_id isolation).
        agent_id: Tenant isolation key.
        existing_node: The old consolidated node to invalidate.
        new_node: The new node to promote.
    """
    existing_id = existing_node["id"]
    new_id = new_node.get("id", new_node.get("node_id", ""))

    # Step 1: Invalidate old node via DAO (UPDATE invalid_at — NOT DELETE)
    # Also cascade-deletes connected KùzuDB edges internally.
    await dao.invalidate_node(agent_id, node_id=existing_id)

    logger.info(
        "CONFLICT_INVALIDATE | agent_id=%s old_node=%s",
        agent_id,
        existing_id,
    )

    # Step 2: Promote new node to consolidated
    await dao.mark_consolidated(agent_id, node_id=new_id)

    logger.info(
        "CONFLICT_PROMOTE | agent_id=%s new_node=%s",
        agent_id,
        new_id,
    )

    # Step 3: Link supersession edge (new_node SUPERSEDES old_node)
    await dao.insert_edge(
        agent_id,
        source_id=new_id,
        target_id=existing_id,
        relation_type="SUPERSEDES",
        weight=1.0,
    )

    logger.info(
        "CONFLICT_LINKED | agent_id=%s edge=%s->%s relation=SUPERSEDES",
        agent_id,
        new_id,
        existing_id,
    )


# ---------------------------------------------------------------------------
# Core REM Cycle Worker
# ---------------------------------------------------------------------------


class REMCycleWorker:
    """Asynchronous background worker for REM consolidation cycles.

    Polls the DAO for unconsolidated records and drives them through
    Dual-LLM consensus conflict resolution when the queue exceeds the
    activation threshold.

    Guarantees:
        1. Consensus pipeline fires ONLY when unconsolidated count > threshold.
        2. Hard per-cycle record budget prevents infinite LLM API cost.
        3. Large queues are processed in FIFO batches across successive cycles.
        4. Conflict resolution never physically deletes — only invalidates.
        5. All operations scoped to agent_id via the DAO layer.

    Args:
        dao: Initialized MemoryDAO instance.
        llm_a: Primary LLM adapter for contradiction detection.
        llm_b: Secondary LLM adapter (independent evaluator).
        agent_ids: List of agent_ids to poll. If None, must be set via
                   ``register_agent`` before starting.
        activation_threshold: Minimum unconsolidated records to trigger
                              the consensus pipeline (default 50).
        max_records_per_cycle: Hard cap on records processed per cycle
                               to prevent LLM cost explosion (default 100).
        poll_interval_seconds: Seconds between queue depth checks (default 30).
        enabled: Set to False to construct without starting.
    """

    def __init__(
        self,
        dao: MemoryDAO,
        llm_a: BaseUniversalLLMAdapter,
        llm_b: BaseUniversalLLMAdapter,
        *,
        agent_ids: list[str] | None = None,
        activation_threshold: int = _DEFAULT_ACTIVATION_THRESHOLD,
        max_records_per_cycle: int = _DEFAULT_MAX_RECORDS_PER_CYCLE,
        poll_interval_seconds: int = _DEFAULT_POLL_INTERVAL_SECONDS,
        enabled: bool = True,
    ) -> None:
        self._dao = dao
        self._llm_a = llm_a
        self._llm_b = llm_b
        self._agent_ids: list[str] = agent_ids or []
        self._activation_threshold = activation_threshold
        self._max_records_per_cycle = max_records_per_cycle
        self._poll_interval = poll_interval_seconds
        self._enabled = enabled
        self._running = False
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._metrics = REMCycleMetrics()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def metrics(self) -> REMCycleMetrics:
        return self._metrics

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def activation_threshold(self) -> int:
        return self._activation_threshold

    @property
    def max_records_per_cycle(self) -> int:
        return self._max_records_per_cycle

    # ------------------------------------------------------------------
    # Agent registration
    # ------------------------------------------------------------------

    def register_agent(self, agent_id: str) -> None:
        """Register an agent_id for REM cycle polling.

        Thread-safe for use during application startup.
        """
        if agent_id not in self._agent_ids:
            self._agent_ids.append(agent_id)
            logger.info("REM_AGENT_REGISTERED | agent_id=%s", agent_id)

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "REMCycleWorker":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Launch the REM cycle loop as a background task.

        Idempotent — calling start() on a running worker is a no-op.
        """
        if self._running or not self._enabled:
            return

        self._stop_event.clear()
        self._running = True
        self._task = asyncio.create_task(
            self._poll_loop(), name="mesa_rem_cycle_worker"
        )
        logger.info(
            "REM_WORKER_STARTED | threshold=%d max_per_cycle=%d "
            "poll_interval=%ds agents=%s",
            self._activation_threshold,
            self._max_records_per_cycle,
            self._poll_interval,
            self._agent_ids,
        )

    async def stop(self) -> None:
        """Gracefully stop the worker and wait for the current cycle."""
        if not self._running:
            return

        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=60)
            except asyncio.TimeoutError:
                logger.warning(
                    "REM_WORKER_STOP_TIMEOUT | "
                    "worker did not stop within 60s, cancelling"
                )
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None

        self._running = False
        logger.info(
            "REM_WORKER_STOPPED | metrics=%s",
            self._metrics.snapshot(),
        )

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Sleep-poll loop that checks queue depth at regular intervals.

        E2 FIX: Dynamically discovers active agent_ids from the DAO on
        each poll cycle, merging with any statically registered agents.
        This ensures the REM worker operates correctly even when started
        with an empty ``agent_ids`` list (the default in server.py).
        """
        # Startup grace period
        try:
            await asyncio.wait_for(
                self._stop_event.wait(),
                timeout=_STARTUP_GRACE_SECONDS,
            )
            return  # stop was called during grace period
        except asyncio.TimeoutError:
            pass

        while not self._stop_event.is_set():
            # E2 FIX: Dynamic agent discovery — merge static + discovered
            active_agents: list[str] = list(self._agent_ids)
            try:
                discovered = await self._dao.get_all_active_agent_ids()
                for aid in discovered:
                    if aid not in active_agents:
                        active_agents.append(aid)
            except Exception as exc:
                logger.warning(
                    "REM_AGENT_DISCOVERY_FAILED | error=%s — using static list",
                    exc,
                )

            for agent_id in active_agents:
                if self._stop_event.is_set():
                    break
                try:
                    await self._process_agent(agent_id)
                except Exception as exc:
                    await self._metrics.record_failure()
                    logger.error(
                        "REM_CYCLE_FAILED | agent_id=%s error=%s",
                        agent_id,
                        exc,
                        exc_info=True,
                    )

            # Sleep until next poll
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._poll_interval,
                )
                break  # stop was called during sleep
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------
    # Per-agent processing
    # ------------------------------------------------------------------

    async def _process_agent(self, agent_id: str) -> None:
        """Check queue depth for an agent and run consolidation if needed."""
        # Fetch ALL unconsolidated records to check queue depth
        unconsolidated = await self._dao.get_memories(
            agent_id,
            include_consolidated=False,
        )

        queue_depth = len(unconsolidated)

        if queue_depth < self._activation_threshold:
            logger.debug(
                "REM_BELOW_THRESHOLD | agent_id=%s depth=%d threshold=%d",
                agent_id,
                queue_depth,
                self._activation_threshold,
            )
            await self._metrics.record_skip()
            return

        # ---- TOKEN BUDGET LIMITER ----
        # Process at most max_records_per_cycle in FIFO order.
        # Records are already ordered by created_at ASC from the DAO.
        batch = unconsolidated[: self._max_records_per_cycle]

        logger.info(
            "REM_CYCLE_START | agent_id=%s queue_depth=%d batch_size=%d (budget=%d)",
            agent_id,
            queue_depth,
            len(batch),
            self._max_records_per_cycle,
        )

        t_start = time.monotonic()
        consolidated_count = 0
        contradicted_count = 0
        promoted_count = 0

        for record in batch:
            if self._stop_event.is_set():
                break

            try:
                was_contradiction = await self._consolidate_record(agent_id, record)
                if was_contradiction:
                    contradicted_count += 1
                    promoted_count += 1
                else:
                    consolidated_count += 1
            except Exception as exc:
                logger.error(
                    "REM_RECORD_FAILED | agent_id=%s node_id=%s error=%s",
                    agent_id,
                    record.get("id", "?"),
                    exc,
                    exc_info=True,
                )

        duration_ms = (time.monotonic() - t_start) * 1000.0

        await self._metrics.record_cycle(
            consolidated=consolidated_count,
            contradicted=contradicted_count,
            promoted=promoted_count,
            duration_ms=duration_ms,
        )

        remaining = queue_depth - len(batch)
        logger.info(
            "REM_CYCLE_COMPLETE | agent_id=%s consolidated=%d "
            "contradicted=%d promoted=%d duration_ms=%.1f "
            "remaining_in_queue=%d",
            agent_id,
            consolidated_count,
            contradicted_count,
            promoted_count,
            duration_ms,
            remaining,
        )

    # ------------------------------------------------------------------
    # Single record consolidation
    # ------------------------------------------------------------------

    async def _consolidate_record(
        self,
        agent_id: str,
        new_node: dict[str, Any],
    ) -> bool:
        """Consolidate a single unconsolidated record.

        Searches for existing consolidated nodes with the same entity_name
        to detect potential contradictions.  If a contradiction is
        confirmed via Dual-LLM consensus, executes conflict resolution.

        Args:
            agent_id: Tenant isolation key.
            new_node: The unconsolidated node dict.

        Returns:
            ``True`` if a contradiction was detected and resolved.
            ``False`` if the record was consolidated without conflict.
        """
        new_id = new_node.get("id", "")
        entity_name = new_node.get("entity_name", "")

        # Search for existing consolidated nodes with matching entity name
        existing_nodes = await self._find_consolidated_matches(agent_id, entity_name)

        if not existing_nodes:
            # No existing match — simply consolidate
            await self._dao.mark_consolidated(agent_id, node_id=new_id)
            logger.debug(
                "REM_CONSOLIDATED_CLEAN | agent_id=%s node_id=%s entity=%s",
                agent_id,
                new_id,
                entity_name,
            )
            return False

        # Check each existing match for contradiction via Dual-LLM consensus
        for existing in existing_nodes:
            try:
                is_contradiction = await evaluate_contradiction(
                    self._llm_a,
                    self._llm_b,
                    existing,
                    new_node,
                )
            except Exception as exc:
                # LLM failure — fail-safe: consolidate without invalidating
                logger.warning(
                    "REM_CONTRADICTION_CHECK_FAILED | agent_id=%s "
                    "existing=%s new=%s error=%s — fail-safe consolidating",
                    agent_id,
                    existing.get("id", "?"),
                    new_id,
                    exc,
                )
                continue

            if is_contradiction:
                # Execute conflict resolution protocol
                await resolve_conflict(self._dao, agent_id, existing, new_node)
                return True

        # No contradiction found with any existing node — simply consolidate
        await self._dao.mark_consolidated(agent_id, node_id=new_id)
        logger.debug(
            "REM_CONSOLIDATED_VERIFIED | agent_id=%s node_id=%s "
            "entity=%s checked_against=%d",
            agent_id,
            new_id,
            entity_name,
            len(existing_nodes),
        )
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _find_consolidated_matches(
        self,
        agent_id: str,
        entity_name: str,
    ) -> list[dict[str, Any]]:
        """Find existing consolidated nodes matching the entity name.

        Delegates to the DAO's ``find_consolidated_nodes_by_name``
        method, which enforces agent_id RLS internally.

        Args:
            agent_id: Tenant isolation key.
            entity_name: Entity name to match against.

        Returns:
            List of matching consolidated node dicts.
        """
        if not entity_name:
            return []

        return await self._dao.find_consolidated_nodes_by_name(
            agent_id, entity_name=entity_name
        )

    # ------------------------------------------------------------------
    # Manual trigger (for testing and ops CLI)
    # ------------------------------------------------------------------

    async def run_now(self, agent_id: str | None = None) -> dict:
        """Manually trigger a REM cycle immediately.

        Args:
            agent_id: If provided, process only this agent.
                      Otherwise, process all registered agents.

        Returns:
            Metrics snapshot after the cycle completes.
        """
        if agent_id:
            await self._process_agent(agent_id)
        else:
            for aid in self._agent_ids:
                await self._process_agent(aid)
        return self._metrics.snapshot()
