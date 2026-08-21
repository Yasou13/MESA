# mypy: disable-error-code="no-untyped-def,untyped-decorator,no-any-return,attr-defined,method-assign"
"""MESA v0.7.1 — Profile A: Safe Core 24H Soak Test & Stability Certification.

Executes sustained real V4 cognitive lifecycle validation under moderate,
predictable load for up to 24 hours without external LLM/API dependencies.

Key Architecture:
    - Model/Embedding Boundary: Deterministic provider (zero remote LLM calls).
    - Runtime & Storage: Real FastAPI, real MESA workers, real SQLite, LanceDB,
      Kùzu graph, mutation ledger, outbox projections, RBAC, and ContextBuilder.
    - Lifecycle Operations: Insert, search recall verification, revisions,
      idempotency checks, rollbacks, document purges, context retrieval,
      and cross-scope isolation audits.
    - Telemetry & Leaks: JSONL telemetry stream, psutil deep memory profiling
      (RSS, VMS, USS, threads, open FDs), real-state queue/backlog tracking,
      bounded drain phase, and zero-tolerance correctness auditing.

.. warning::

    Runs under 24 hours (86,400 seconds) produce `SMOKE PASS — NOT CERTIFICATION`.
    Production certification strictly requires a completed 24-hour window with
    zero critical correctness violations, clean drain, and verified health.

Usage:
    # 5-minute smoke test
    python -m mesa_evals.soak_test --duration 300

    # 30-minute smoke test
    python -m mesa_evals.soak_test --duration 1800

    # 24-hour Profile A certification
    python -m mesa_evals.soak_test --duration 86400

    # Remote instance testing
    python -m mesa_evals.soak_test --base-url http://prod:8000 --api-key SECRET --server-pid 1234
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import random
import re
import resource
import signal
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import configured_embedding_identity
from mesa_memory.embedding.service import EmbeddingIdentity, EmbeddingService

logger = logging.getLogger("MESA_SoakTest")

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_DURATION = 86_400  # 24 hours = 86,400 seconds
DEFAULT_RPS = 2.0  # sustainable operations per second for 24h
DEFAULT_TELEMETRY_INTERVAL = 60.0  # seconds between telemetry snapshots
DEFAULT_DRAIN_TIMEOUT = 30.0  # seconds to wait for in-flight work to drain
DEFAULT_TENANT_ID = "soak-tenant"
DEFAULT_WORKSPACE_ID = "default"
DEFAULT_DATASET_ID = "soak-dataset"
DEFAULT_AGENT_ID = "soak-agent"
DEFAULT_PRINCIPAL_ID = "soak-principal"

# ---------------------------------------------------------------------------
# Production certification thresholds & invariants
# ---------------------------------------------------------------------------

_MIN_PRODUCTION_DURATION = 86_400  # 24 hours minimum for certification
MAX_CONSECUTIVE_HEALTH_FAILURES = 3
MAX_OPERATIONAL_FAILURE_RATE = 0.05


def is_production_certification_duration(duration: float) -> bool:
    """Return whether a requested soak duration meets the release contract."""
    return duration >= _MIN_PRODUCTION_DURATION


# ---------------------------------------------------------------------------
# Deterministic model & embedding provider boundary
# ---------------------------------------------------------------------------


class DeterministicSoakProvider(BaseUniversalLLMAdapter):
    """Deterministic LLM and extraction adapter for Profile A soak testing.

    Ensures zero external API dependency while producing non-empty canonical
    facts and normalized embeddings for end-to-end V4 lifecycle verification.
    """

    def __init__(self, model_name: str = "mesa-soak-deterministic-provider") -> None:
        self.model_name = model_name
        self.completions = 0
        self.embeddings = 0

    def complete(self, prompt: str, schema: Any = None, **_: Any) -> Any:
        self.completions += 1
        if schema is not None:
            if "<UNTRUSTED_SOURCE>\n" in prompt:
                text = prompt.rsplit("<UNTRUSTED_SOURCE>\n", 1)[-1].split(
                    "\n</UNTRUSTED_SOURCE>", 1
                )[0]
            else:
                text = prompt

            link_match = re.search(
                r"(SOAK-[A-Z0-9_\-]+)\s+is linked to\s+([A-Z0-9_\-]+)",
                text,
                re.IGNORECASE,
            )
            update_match = re.search(
                r"(SOAK-[A-Z0-9_\-]+)\s+is updated to\s+([A-Za-z0-9_\-]+)",
                text,
                re.IGNORECASE,
            )

            if link_match:
                subj = link_match.group(1).upper()
                pred = "LINKED_TO"
                obj = link_match.group(2).upper()
                fact_text = f"{subj} is linked to {obj}."
                source_span = link_match.group(0)
            elif update_match:
                subj = update_match.group(1).upper()
                pred = "UPDATED_TO"
                obj = update_match.group(2).upper()
                fact_text = f"{subj} is updated to {obj}."
                source_span = update_match.group(0)
            else:
                subj = "SOAK-SUBJECT"
                pred = "RECORDED"
                obj = "SOAK-VALUE"
                fact_text = text[:120].strip() if text else "MESA soak fact"
                source_span = text[:120].strip() if text else "soak"

            fact_data = {
                "facts": [
                    {
                        "fact_text": fact_text,
                        "subject": subj,
                        "predicate": pred,
                        "object": obj,
                        "confidence": 1.0,
                        "source_span": source_span,
                        "valid_from": None,
                        "valid_to": None,
                        "supersedes": None,
                        "metadata": {"soak_deterministic": True},
                    }
                ]
            }
            if hasattr(schema, "model_validate"):
                return schema.model_validate(fact_data)
            return fact_data

        return '{"decision":"STORE","justification":"deterministic soak provider"}'

    async def acomplete(self, prompt: str, schema: Any = None, **kwargs: Any) -> Any:
        return self.complete(prompt, schema, **kwargs)

    def embed(self, text: str, **_: Any) -> list[float]:
        self.embeddings += 1
        h = hashlib.sha256(text.encode("utf-8")).digest()
        dim = 384
        vec = [(b / 255.0) - 0.5 for b in h]
        if len(vec) < dim:
            vec = vec * (dim // len(vec) + 1)
        vec = vec[:dim]
        norm = sum(x * x for x in vec) ** 0.5
        if norm == 0:
            return [1.0] + [0.0] * (dim - 1)
        return [x / norm for x in vec]

    async def aembed(self, text: str, **kwargs: Any) -> list[float]:
        return self.embed(text, **kwargs)

    def embed_batch(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [self.embed(t, **kwargs) for t in texts]

    async def aembed_batch(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return self.embed_batch(texts, **kwargs)

    def get_token_count(self, text: str) -> int:
        return len(text.split())


def _make_embedding_service(provider: DeterministicSoakProvider) -> EmbeddingService:
    configured = configured_embedding_identity()
    return EmbeddingService(
        identity=EmbeddingIdentity(
            provider=configured.provider,
            model=configured.model,
            dimension=configured.dimension,
            version=configured.version,
            normalized=configured.normalized,
            model_revision=configured.model_revision,
        ),
        provider_fn=provider.embed,
        allow_model_loading=False,
        external_enabled=True,
    )


# ---------------------------------------------------------------------------
# Data structures & Metrics accumulator
# ---------------------------------------------------------------------------


@dataclass
class SoakItem:
    """Track an admitted memory item through its lifecycle."""

    seq: int
    doc_id: str
    rev_id: str
    chunk_id: str
    subj: str
    obj: str
    content: str
    mutation_id: str
    idempotency_key: str | None
    dataset_id: str
    session_id: str
    revision_number: int = 1
    state: str = "COMMITTED"
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class SoakMetrics:
    """Thread-safe accumulator for Profile A soak test telemetry."""

    total_operations: int = 0
    successful_operations: int = 0
    failed_operations: int = 0
    http_errors: dict[int, int] = field(default_factory=dict)

    # Operation counts
    insert_count: int = 0
    search_count: int = 0
    revision_count: int = 0
    idempotent_insert_count: int = 0
    rollback_count: int = 0
    purge_count: int = 0
    context_count: int = 0
    cross_scope_count: int = 0

    # Latencies in ms
    insert_latencies_ms: list[float] = field(default_factory=list)
    search_latencies_ms: list[float] = field(default_factory=list)
    commit_latencies_ms: list[float] = field(default_factory=list)
    context_latencies_ms: list[float] = field(default_factory=list)

    # Correctness counters (zero tolerance in Profile A)
    wrong_retrieval_count: int = 0
    missing_committed_memory_count: int = 0
    deleted_memory_visible_count: int = 0
    cross_scope_result_count: int = 0
    consistency_failure_count: int = 0

    # Runtime queue / backlog stats (None when unmeasured, never fake 0)
    pending_mutations: int | None = None
    failed_mutations: int | None = None
    dead_letters: int | None = None
    projection_backlog: int | None = None

    # Periodic health check tracking
    health_checks_total: int = 0
    health_checks_failed: int = 0
    consecutive_health_failures: int = 0
    max_consecutive_health_failures: int = 0
    final_health: str = "unknown"

    # Resource profiling
    resource_samples: list[dict[str, Any]] = field(default_factory=list)
    peak_rss_mb: float | None = None
    start_resources: dict[str, Any] = field(default_factory=dict)
    latest_resources: dict[str, Any] = field(default_factory=dict)

    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def record_op(
        self,
        op_type: str,
        *,
        success: bool,
        latency_ms: float = 0.0,
        status_code: int = 200,
    ) -> None:
        async with self._lock:
            self.total_operations += 1
            if success:
                self.successful_operations += 1
            else:
                self.failed_operations += 1
                self.http_errors[status_code] = self.http_errors.get(status_code, 0) + 1

            if op_type == "insert":
                self.insert_count += 1
                if latency_ms > 0:
                    self.insert_latencies_ms.append(latency_ms)
            elif op_type == "search":
                self.search_count += 1
                if latency_ms > 0:
                    self.search_latencies_ms.append(latency_ms)
            elif op_type == "revision":
                self.revision_count += 1
            elif op_type == "idempotent_insert":
                self.idempotent_insert_count += 1
            elif op_type == "rollback":
                self.rollback_count += 1
            elif op_type == "purge":
                self.purge_count += 1
            elif op_type == "context":
                self.context_count += 1
                if latency_ms > 0:
                    self.context_latencies_ms.append(latency_ms)
            elif op_type == "cross_scope":
                self.cross_scope_count += 1

    async def record_commit(self, latency_ms: float) -> None:
        async with self._lock:
            self.commit_latencies_ms.append(latency_ms)

    async def record_health_check(self, is_healthy: bool, status_str: str) -> None:
        async with self._lock:
            self.health_checks_total += 1
            if is_healthy:
                self.consecutive_health_failures = 0
            else:
                self.health_checks_failed += 1
                self.consecutive_health_failures += 1
                if (
                    self.consecutive_health_failures
                    > self.max_consecutive_health_failures
                ):
                    self.max_consecutive_health_failures = (
                        self.consecutive_health_failures
                    )

    async def record_correctness_violation(self, kind: str) -> None:
        async with self._lock:
            if kind == "wrong_retrieval":
                self.wrong_retrieval_count += 1
            elif kind == "missing_committed_memory":
                self.missing_committed_memory_count += 1
            elif kind == "deleted_memory_visible":
                self.deleted_memory_visible_count += 1
            elif kind == "cross_scope_results":
                self.cross_scope_result_count += 1
            elif kind == "consistency_failures":
                self.consistency_failure_count += 1

    async def update_runtime_state(self, state: dict[str, int | None]) -> None:
        async with self._lock:
            if "pending_mutations" in state:
                self.pending_mutations = state["pending_mutations"]
            if "failed_mutations" in state:
                self.failed_mutations = state["failed_mutations"]
            if "dead_letters" in state:
                self.dead_letters = state["dead_letters"]
            if "projection_backlog" in state:
                self.projection_backlog = state["projection_backlog"]

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:

            def _stats(vals: list[float]) -> dict[str, float]:
                if not vals:
                    return {"p50_ms": 0.0, "p99_ms": 0.0, "mean_ms": 0.0}
                s = sorted(vals)
                n = len(s)
                return {
                    "p50_ms": round(s[n // 2], 2),
                    "p99_ms": round(s[int(n * 0.99)], 2),
                    "mean_ms": round(sum(s) / n, 2),
                }

            return {
                "total_operations": self.total_operations,
                "successful_operations": self.successful_operations,
                "failed_operations": self.failed_operations,
                "success_ratio": (
                    round(self.successful_operations / self.total_operations, 6)
                    if self.total_operations > 0
                    else 1.0
                ),
                "http_errors": dict(self.http_errors),
                "counts": {
                    "insert": self.insert_count,
                    "search": self.search_count,
                    "revision": self.revision_count,
                    "idempotent_insert": self.idempotent_insert_count,
                    "rollback": self.rollback_count,
                    "purge": self.purge_count,
                    "context": self.context_count,
                    "cross_scope": self.cross_scope_count,
                },
                "latencies": {
                    "insert": _stats(self.insert_latencies_ms),
                    "search": _stats(self.search_latencies_ms),
                    "commit": _stats(self.commit_latencies_ms),
                    "context": _stats(self.context_latencies_ms),
                },
                "correctness": {
                    "wrong_retrieval": self.wrong_retrieval_count,
                    "missing_committed_memory": self.missing_committed_memory_count,
                    "deleted_memory_visible": self.deleted_memory_visible_count,
                    "cross_scope_results": self.cross_scope_result_count,
                    "consistency_failures": self.consistency_failure_count,
                },
                "runtime": {
                    "pending_mutations": self.pending_mutations,
                    "failed_mutations": self.failed_mutations,
                    "dead_letters": self.dead_letters,
                    "projection_backlog": self.projection_backlog,
                },
                "health": {
                    "health_checks_total": self.health_checks_total,
                    "health_checks_failed": self.health_checks_failed,
                    "consecutive_health_failures": self.consecutive_health_failures,
                    "max_consecutive_health_failures": self.max_consecutive_health_failures,
                    "final_health": self.final_health,
                },
            }


# ---------------------------------------------------------------------------
# Real Runtime State Querying (Direct SQL / Prometheus)
# ---------------------------------------------------------------------------


async def query_runtime_state(
    client: httpx.AsyncClient,
    dao: Any | None = None,
) -> dict[str, int | None]:
    """Query actual MESA state for mutation queues, projection outbox, and dead letters.

    If running embedded with access to DAO, queries SQLite tables directly.
    If running remote, queries /metrics endpoint for projection backlog/DLQ.
    Returns integer values if measurable, or None (null) if unavailable.
    """
    if dao is not None and hasattr(dao, "_sql"):
        try:
            async with dao._sql.connection() as db:
                async with db.execute(
                    "SELECT state, COUNT(*) FROM memory_mutations GROUP BY state"
                ) as cursor:
                    m_states = {row[0]: row[1] for row in await cursor.fetchall()}

                async with db.execute(
                    "SELECT state, COUNT(*) FROM projection_outbox GROUP BY state"
                ) as cursor:
                    outbox_states = {row[0]: row[1] for row in await cursor.fetchall()}

            pending_mutations = sum(
                m_states.get(s, 0)
                for s in (
                    "PENDING",
                    "ADMITTED",
                    "PROCESSING",
                    "EXTRACTING",
                    "VALIDATING",
                    "INDEXING",
                    "ROLLING_BACK",
                )
            )
            failed_mutations = m_states.get("FAILED", 0) + m_states.get("REJECTED", 0)
            projection_backlog = sum(
                outbox_states.get(s, 0)
                for s in ("PENDING", "RETRY_PENDING", "IN_FLIGHT")
            )
            dead_letters = outbox_states.get("DEAD_LETTER", 0)

            return {
                "pending_mutations": pending_mutations,
                "failed_mutations": failed_mutations,
                "projection_backlog": projection_backlog,
                "dead_letters": dead_letters,
            }
        except Exception as exc:
            logger.debug("RUNTIME_STATE_SQL_ERROR | %s", exc)
            return {
                "pending_mutations": None,
                "failed_mutations": None,
                "projection_backlog": None,
                "dead_letters": None,
            }

    # Remote mode: check Prometheus /metrics endpoint
    try:
        resp = await client.get("/metrics")
        if resp.status_code == 200:
            text = resp.text
            backlog = None
            dlq = None
            for line in text.splitlines():
                if line.startswith("mesa_v4_projection_backlog "):
                    backlog = int(float(line.split()[1]))
                elif line.startswith("mesa_v4_projection_dlq "):
                    dlq = int(float(line.split()[1]))
            return {
                "pending_mutations": None,
                "failed_mutations": None,
                "projection_backlog": backlog,
                "dead_letters": dlq,
            }
    except Exception:
        pass

    return {
        "pending_mutations": None,
        "failed_mutations": None,
        "projection_backlog": None,
        "dead_letters": None,
    }


# ---------------------------------------------------------------------------
# Resource profiling & Leak detection
# ---------------------------------------------------------------------------


def get_process_resources(
    pid: int | None = None,
    *,
    is_embedded: bool = True,
) -> dict[str, Any]:
    """Retrieve process resource metrics (RSS, VMS, USS, threads, FDs).

    If in remote mode without an explicit target server PID, reports unavailable.
    """
    if not is_embedded and pid is None:
        return {
            "status": "unavailable",
            "reason": "remote_mode_without_server_pid",
            "rss_mb": None,
            "vms_mb": None,
            "uss_mb": None,
            "open_fds": None,
            "threads": None,
            "cpu_percent": None,
        }

    target_pid = pid if pid is not None else os.getpid()
    try:
        import psutil

        if not psutil.pid_exists(target_pid):
            return {
                "status": "unavailable",
                "pid": target_pid,
                "reason": "pid_does_not_exist",
                "rss_mb": None,
                "vms_mb": None,
                "uss_mb": None,
                "open_fds": None,
                "threads": None,
                "cpu_percent": None,
            }

        proc = psutil.Process(target_pid)
        procs = [proc]
        try:
            procs.extend(proc.children(recursive=True))
        except Exception:
            pass

        total_rss = 0.0
        total_vms = 0.0
        total_uss = 0.0
        total_threads = 0
        total_fds = 0
        total_cpu = 0.0

        for p in procs:
            try:
                mem = p.memory_full_info()
                total_rss += mem.rss / (1024 * 1024)
                total_vms += mem.vms / (1024 * 1024)
                total_uss += getattr(mem, "uss", mem.rss) / (1024 * 1024)
                if hasattr(p, "num_fds"):
                    total_fds += p.num_fds()
                total_threads += p.num_threads()
                total_cpu += p.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        return {
            "status": "available",
            "pid": target_pid,
            "rss_mb": round(total_rss, 2),
            "vms_mb": round(total_vms, 2),
            "uss_mb": round(total_uss, 2),
            "open_fds": total_fds if hasattr(proc, "num_fds") else None,
            "threads": total_threads,
            "cpu_percent": round(total_cpu, 1),
        }
    except ImportError:
        try:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            rss = (
                usage.ru_maxrss / (1024 * 1024)
                if sys.platform == "darwin"
                else usage.ru_maxrss / 1024
            )
            return {
                "status": "partial_psutil_missing",
                "pid": target_pid,
                "rss_mb": round(rss, 2),
                "vms_mb": None,
                "uss_mb": None,
                "open_fds": None,
                "threads": None,
                "cpu_percent": None,
            }
        except Exception:
            return {
                "status": "unavailable",
                "pid": target_pid,
                "rss_mb": None,
                "vms_mb": None,
                "uss_mb": None,
                "open_fds": None,
                "threads": None,
                "cpu_percent": None,
            }
    except Exception as exc:
        return {
            "status": "unavailable",
            "pid": target_pid,
            "error": str(exc),
            "rss_mb": None,
            "vms_mb": None,
            "uss_mb": None,
            "open_fds": None,
            "threads": None,
            "cpu_percent": None,
        }


def evaluate_resource_trends(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze periodic samples to detect memory, thread, or FD leak trends.

    Accounts for warm-up phase (first 25% of samples) and checks for
    steady linear monotonic growth in the mature phase.
    """
    valid_samples = [
        s
        for s in samples
        if s.get("status") in {"available", "partial_psutil_missing"}
        and s.get("rss_mb") is not None
    ]
    if len(valid_samples) < 4:
        return {
            "evaluated": False,
            "possible_memory_leak": False,
            "thread_leak_detected": False,
            "fd_leak_detected": False,
            "details": "Insufficient valid resource samples (< 4)",
        }

    # Discard initial warm-up period (first 25% of samples)
    warmup_cutoff = max(1, len(valid_samples) // 4)
    mature_samples = valid_samples[warmup_cutoff:]
    if len(mature_samples) < 3:
        mature_samples = valid_samples

    rss_values = [s["rss_mb"] for s in mature_samples if s.get("rss_mb") is not None]
    fd_values = [
        s["open_fds"]
        for s in mature_samples
        if s.get("open_fds") is not None and s["open_fds"] >= 0
    ]
    thread_values = [
        s["threads"]
        for s in mature_samples
        if s.get("threads") is not None and s["threads"] > 0
    ]

    possible_memory_leak = False
    if len(rss_values) >= 4:
        start_rss = rss_values[0]
        final_rss = rss_values[-1]
        growth = final_rss - start_rss
        recent_rss = rss_values[-4:]
        is_monotonic = all(
            recent_rss[i] <= recent_rss[i + 1] for i in range(len(recent_rss) - 1)
        )
        if growth > 50.0 and final_rss > start_rss * 1.5 and is_monotonic:
            possible_memory_leak = True

    fd_leak = False
    if len(fd_values) >= 4:
        fd_growth = fd_values[-1] - fd_values[0]
        recent_fds = fd_values[-4:]
        is_fd_monotonic = all(
            recent_fds[i] <= recent_fds[i + 1] for i in range(len(recent_fds) - 1)
        )
        if fd_growth > 50 and is_fd_monotonic:
            fd_leak = True

    thread_leak = False
    if len(thread_values) >= 4:
        th_growth = thread_values[-1] - thread_values[0]
        recent_th = thread_values[-4:]
        is_th_monotonic = all(
            recent_th[i] <= recent_th[i + 1] for i in range(len(recent_th) - 1)
        )
        if th_growth > 20 and is_th_monotonic:
            thread_leak = True

    return {
        "evaluated": True,
        "possible_memory_leak": possible_memory_leak,
        "thread_leak_detected": thread_leak,
        "fd_leak_detected": fd_leak,
    }


# ---------------------------------------------------------------------------
# V4 In-process runtime setup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def setup_soak_runtime(
    storage_root: Path,
    *,
    api_key: str = "soak-test-key",
    principal_id: str = DEFAULT_PRINCIPAL_ID,
    tenant_id: str = DEFAULT_TENANT_ID,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    dataset_id: str = DEFAULT_DATASET_ID,
    agent_id: str = DEFAULT_AGENT_ID,
) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    """Initialize and run the full combined MESA V4 runtime in-process."""
    from mesa_memory.api import server
    from mesa_memory.config import refresh_config_from_environment
    from mesa_memory.embedding.service import set_global_embedding_service

    env_overrides = {
        "MESA_RUNTIME_PROFILE": "combined",
        "MESA_STORAGE_ROOT": str(storage_root),
        "MESA_LOAD_DOTENV": "false",
        "MESA_MODEL_ENABLED": "true",
        "MESA_EXTERNAL_PROVIDER_ENABLED": "true",
        "MESA_TIER3_MODE": "0",
        "MESA_REBEL_ENABLED": "false",
        "MESA_EMBEDDING_DIMENSION": "384",
        "MESA_LLM_PROVIDER": "mock",
        "MESA_API_KEY": api_key,
        "MESA_PRINCIPAL_ID": principal_id,
        "MESA_PRINCIPAL_STATUS": "active",
    }
    orig_env = {k: os.environ.get(k) for k in env_overrides}

    storage_root.mkdir(parents=True, exist_ok=True)
    os.environ.update(env_overrides)
    refresh_config_from_environment()

    provider = DeterministicSoakProvider()
    orig_get_adapter = server.AdapterFactory.get_adapter
    orig_embedding_service = server._get_embedding_service

    server.AdapterFactory.get_adapter = staticmethod(lambda *args, **kwargs: provider)
    server._get_embedding_service = lambda **_kwargs: _make_embedding_service(provider)

    from mesa_memory.extraction import rebel_pipeline

    rebel_pipeline._model_holder.reset()
    orig_pipeline = getattr(rebel_pipeline, "pipeline", None)
    rebel_pipeline.pipeline = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("fake REBEL unavailable")
    )

    try:
        async with server.lifespan(server.app):
            dao = server.state.dao
            ac = server.state.access_control

            # Bootstrap Scope A (Primary Dataset)
            await dao.ensure_v4_catalog_scope(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
            )
            await ac.grant_scope_role(
                principal_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                role="OWNER",
            )
            await ac.grant_principal_permission(
                principal_id, agent_id, "SESSION_CREATE"
            )
            await ac.grant_dataset_permission(
                principal_id,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                permission="PURGE",
            )
            await ac.grant_dataset_permission(
                principal_id,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                permission="ROLLBACK",
            )

            # Bootstrap Scope B (Secondary Dataset for cross-scope isolation audit)
            dataset_b_id = f"{dataset_id}-b"
            agent_b_id = f"{agent_id}-b"
            await dao.ensure_v4_catalog_scope(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_b_id,
            )
            await ac.grant_scope_role(
                principal_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_b_id,
                role="OWNER",
            )
            await ac.grant_principal_permission(
                principal_id, agent_b_id, "SESSION_CREATE"
            )
            await ac.grant_dataset_permission(
                principal_id,
                tenant_id=tenant_id,
                dataset_id=dataset_b_id,
                permission="PURGE",
            )
            await ac.grant_dataset_permission(
                principal_id,
                tenant_id=tenant_id,
                dataset_id=dataset_b_id,
                permission="ROLLBACK",
            )

            await ac.grant_control_role(principal_id, role="ADMIN")

            transport = httpx.ASGITransport(app=server.app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://mesa-soak",
                headers={"X-API-Key": api_key},
                timeout=30.0,
            ) as client:
                yield client, server.state
    finally:
        for k, v in orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        refresh_config_from_environment()
        set_global_embedding_service(None)
        server.AdapterFactory.get_adapter = orig_get_adapter
        server._get_embedding_service = orig_embedding_service
        if orig_pipeline is not None:
            rebel_pipeline.pipeline = orig_pipeline
        rebel_pipeline._model_holder.reset()


# ---------------------------------------------------------------------------
# Workload driver & Lifecycle operations
# ---------------------------------------------------------------------------


async def _poll_mutation_state(
    client: httpx.AsyncClient,
    mutation_id: str,
    target_states: set[str],
    *,
    timeout: float = 20.0,
) -> tuple[bool, float, dict[str, Any]]:
    """Poll GET /v4/mutations/{mutation_id} until state in target_states or timeout."""
    t0 = time.monotonic()
    deadline = t0 + timeout
    while time.monotonic() < deadline:
        try:
            resp = await client.get(f"/v4/mutations/{mutation_id}")
            if resp.status_code == 200:
                data = resp.json()
                state = data.get("state")
                if state in target_states:
                    latency_ms = (time.monotonic() - t0) * 1000
                    return True, latency_ms, data
                if state in {"FAILED", "REJECTED"} and state not in target_states:
                    return False, (time.monotonic() - t0) * 1000, data
        except Exception:
            pass
        await asyncio.sleep(0.1)
    return False, (time.monotonic() - t0) * 1000, {"state": "TIMEOUT"}


async def _poll_mutation_committed(
    client: httpx.AsyncClient,
    mutation_id: str,
    *,
    timeout: float = 20.0,
) -> tuple[bool, float, dict[str, Any]]:
    """Poll GET /v4/mutations/{mutation_id} until COMMITTED or timeout."""
    return await _poll_mutation_state(
        client, mutation_id, {"COMMITTED"}, timeout=timeout
    )


def _is_subject_in_search_results(
    expected_subj: str, results: list[dict[str, Any]]
) -> bool:
    """Check if expected subject entity or assertion is present in V4 search results."""
    for r in results:
        entity = r.get("entity", {})
        if (
            entity.get("canonical_name") == expected_subj
            or entity.get("name") == expected_subj
            or entity.get("normalized_name") == expected_subj.lower()
        ):
            return True
        for prov in r.get("provenance", []):
            if expected_subj in str(prov.get("fact_text", "")) or expected_subj in str(
                prov.get("predicate", "")
            ):
                return True
    return False


async def _op_insert(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    dataset_id: str,
    seq: int,
    metrics: SoakMetrics,
    active_items: list[SoakItem],
) -> None:
    doc_id = f"soak-doc-{seq:06d}"
    rev_id = f"soak-rev-{seq:06d}-1"
    chunk_id = f"soak-chk-{seq:06d}-1"
    subj = f"SOAK-{seq:06d}"
    obj = f"CASE-{seq:06d}"
    content = (
        f"{subj} is linked to {obj}. Türk hukuku delil tespiti ve emsal karar kaydı "
        f"tarih={datetime.now(timezone.utc).isoformat()}."
    )
    idemp_key = f"idemp-soak-{seq:06d}"

    payload = {
        "session_id": session_id,
        "dataset_id": dataset_id,
        "document_id": doc_id,
        "revision_id": rev_id,
        "chunk_id": chunk_id,
        "title": f"Soak Kaydı #{seq}",
        "source_ref": "soak-v4",
        "content": content,
        "evidence_span": f"0:{len(subj) + len(obj) + 14}",
        "revision_number": 1,
        "chunk_ordinal": 0,
        "finalize_revision": True,
        "metadata": {"soak_seq": seq, "op": "insert"},
        "idempotency_key": idemp_key,
    }

    t0 = time.monotonic()
    try:
        resp = await client.post("/v4/memory/insert", json=payload)
        lat_ms = (time.monotonic() - t0) * 1000
        if resp.status_code != 202:
            await metrics.record_op(
                "insert", success=False, latency_ms=lat_ms, status_code=resp.status_code
            )
            return

        body = resp.json()
        mutation_id = body.get("mutation_id")
        await metrics.record_op(
            "insert", success=True, latency_ms=lat_ms, status_code=202
        )

        if not mutation_id:
            await metrics.record_correctness_violation("consistency_failures")
            return

        committed, commit_lat, _ = await _poll_mutation_committed(client, mutation_id)
        if not committed:
            await metrics.record_correctness_violation("missing_committed_memory")
            return

        await metrics.record_commit(commit_lat)

        # Immediate verification search with bounded retry
        found = False
        for _ in range(3):
            s_resp = await client.post(
                "/v4/memory/search",
                json={
                    "session_id": session_id,
                    "dataset_ids": [dataset_id],
                    "query": subj,
                    "limit": 5,
                },
            )
            if s_resp.status_code == 200:
                results = s_resp.json().get("results", [])
                if _is_subject_in_search_results(subj, results):
                    found = True
                    break
            await asyncio.sleep(0.1)

        if not found:
            await metrics.record_correctness_violation("missing_committed_memory")
            await metrics.record_correctness_violation("wrong_retrieval")

        active_items.append(
            SoakItem(
                seq=seq,
                doc_id=doc_id,
                rev_id=rev_id,
                chunk_id=chunk_id,
                subj=subj,
                obj=obj,
                content=content,
                mutation_id=mutation_id,
                idempotency_key=idemp_key,
                dataset_id=dataset_id,
                session_id=session_id,
            )
        )
    except Exception as exc:
        lat_ms = (time.monotonic() - t0) * 1000
        logger.debug("INSERT_OP_ERROR | seq=%d error=%s", seq, exc)
        await metrics.record_op(
            "insert", success=False, latency_ms=lat_ms, status_code=0
        )


async def _op_search(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    dataset_id: str,
    metrics: SoakMetrics,
    active_items: list[SoakItem],
) -> None:
    valid_candidates = [it for it in active_items if it.state == "COMMITTED"]
    if not valid_candidates:
        query = "soak"
        expected_subj = None
    else:
        target = random.choice(valid_candidates)
        query = target.subj
        expected_subj = target.subj

    t0 = time.monotonic()
    try:
        resp = await client.post(
            "/v4/memory/search",
            json={
                "session_id": session_id,
                "dataset_ids": [dataset_id],
                "query": query,
                "limit": 5,
            },
        )
        lat_ms = (time.monotonic() - t0) * 1000
        if resp.status_code != 200:
            await metrics.record_op(
                "search", success=False, latency_ms=lat_ms, status_code=resp.status_code
            )
            return

        await metrics.record_op(
            "search", success=True, latency_ms=lat_ms, status_code=200
        )
        if expected_subj is not None:
            results = resp.json().get("results", [])
            if not _is_subject_in_search_results(expected_subj, results):
                await metrics.record_correctness_violation("wrong_retrieval")
    except Exception as exc:
        lat_ms = (time.monotonic() - t0) * 1000
        logger.debug("SEARCH_OP_ERROR | query=%s error=%s", query, exc)
        await metrics.record_op(
            "search", success=False, latency_ms=lat_ms, status_code=0
        )


async def _op_revision(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    dataset_id: str,
    metrics: SoakMetrics,
    active_items: list[SoakItem],
) -> None:
    candidates = [
        it for it in active_items if it.revision_number == 1 and it.state == "COMMITTED"
    ]
    if not candidates:
        return

    item = random.choice(candidates)
    new_rev_id = f"soak-rev-{item.seq:06d}-2"
    new_chunk_id = f"soak-chk-{item.seq:06d}-2"
    new_obj = f"STATUS-RESOLVED-{item.seq:06d}"
    new_content = (
        f"{item.subj} is updated to {new_obj}. Revize edilmiş Türk mevzuatı kaydı "
        f"tarih={datetime.now(timezone.utc).isoformat()}."
    )

    payload = {
        "session_id": session_id,
        "dataset_id": dataset_id,
        "document_id": item.doc_id,
        "revision_id": new_rev_id,
        "chunk_id": new_chunk_id,
        "title": f"Soak Kaydı #{item.seq} (Rev 2)",
        "source_ref": "soak-v4",
        "content": new_content,
        "revision_number": 2,
        "chunk_ordinal": 0,
        "finalize_revision": True,
        "supersedes_revision_id": item.rev_id,
        "metadata": {"soak_seq": item.seq, "op": "revision"},
    }

    t0 = time.monotonic()
    try:
        resp = await client.post("/v4/memory/insert", json=payload)
        lat_ms = (time.monotonic() - t0) * 1000
        if resp.status_code != 202:
            await metrics.record_op(
                "revision",
                success=False,
                latency_ms=lat_ms,
                status_code=resp.status_code,
            )
            return

        body = resp.json()
        mutation_id = body.get("mutation_id")
        await metrics.record_op(
            "revision", success=True, latency_ms=lat_ms, status_code=202
        )

        if mutation_id:
            committed, _, _ = await _poll_mutation_committed(client, mutation_id)
            if committed:
                item.rev_id = new_rev_id
                item.revision_number = 2
                item.obj = new_obj
                item.content = new_content
                item.mutation_id = mutation_id
            else:
                await metrics.record_correctness_violation("missing_committed_memory")
    except Exception as exc:
        lat_ms = (time.monotonic() - t0) * 1000
        logger.debug("REVISION_OP_ERROR | seq=%d error=%s", item.seq, exc)
        await metrics.record_op(
            "revision", success=False, latency_ms=lat_ms, status_code=0
        )


async def _op_idempotency(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    dataset_id: str,
    metrics: SoakMetrics,
    active_items: list[SoakItem],
) -> None:
    candidates = [
        it
        for it in active_items
        if it.idempotency_key is not None
        and it.revision_number == 1
        and it.state == "COMMITTED"
    ]
    if not candidates:
        return

    item = random.choice(candidates)
    payload = {
        "session_id": session_id,
        "dataset_id": dataset_id,
        "document_id": item.doc_id,
        "revision_id": item.rev_id,
        "chunk_id": item.chunk_id,
        "title": f"Soak Kaydı #{item.seq}",
        "source_ref": "soak-v4",
        "content": item.content,
        "evidence_span": f"0:{len(item.subj) + len(item.obj) + 14}",
        "revision_number": 1,
        "chunk_ordinal": 0,
        "finalize_revision": True,
        "metadata": {"soak_seq": item.seq, "op": "insert"},
        "idempotency_key": item.idempotency_key,
    }

    t0 = time.monotonic()
    try:
        resp = await client.post("/v4/memory/insert", json=payload)
        lat_ms = (time.monotonic() - t0) * 1000
        if resp.status_code == 202:
            body = resp.json()
            is_dup = body.get("duplicate") is True
            same_mut = body.get("mutation_id") == item.mutation_id
            if is_dup and same_mut:
                await metrics.record_op(
                    "idempotent_insert",
                    success=True,
                    latency_ms=lat_ms,
                    status_code=202,
                )
            else:
                await metrics.record_correctness_violation("consistency_failures")
                await metrics.record_op(
                    "idempotent_insert",
                    success=False,
                    latency_ms=lat_ms,
                    status_code=202,
                )
        else:
            await metrics.record_op(
                "idempotent_insert",
                success=False,
                latency_ms=lat_ms,
                status_code=resp.status_code,
            )
    except Exception as exc:
        lat_ms = (time.monotonic() - t0) * 1000
        logger.debug("IDEMPOTENCY_OP_ERROR | seq=%d error=%s", item.seq, exc)
        await metrics.record_op(
            "idempotent_insert", success=False, latency_ms=lat_ms, status_code=0
        )


async def _op_rollback(
    client: httpx.AsyncClient,
    *,
    metrics: SoakMetrics,
    active_items: list[SoakItem],
) -> None:
    # Only choose items that are in COMMITTED state and at revision 2
    candidates = [
        it for it in active_items if it.revision_number == 2 and it.state == "COMMITTED"
    ]
    if not candidates:
        return

    item = random.choice(candidates)
    item.state = "ROLLING_BACK"  # immediately take out of candidate pool
    t0 = time.monotonic()
    try:
        resp = await client.post(f"/v4/mutations/{item.mutation_id}/rollback")
        lat_ms = (time.monotonic() - t0) * 1000
        if resp.status_code == 202:
            # Poll for rollback completion
            rb_ok, rb_lat, rb_data = await _poll_mutation_state(
                client, item.mutation_id, {"ROLLED_BACK"}, timeout=10.0
            )
            if rb_ok or rb_data.get("state") == "ROLLED_BACK":
                item.state = "ROLLED_BACK"
                item.revision_number = 1
                await metrics.record_op(
                    "rollback",
                    success=True,
                    latency_ms=lat_ms + rb_lat,
                    status_code=202,
                )
            else:
                item.state = "ROLLBACK_UNCONFIRMED"
                await metrics.record_op(
                    "rollback", success=False, latency_ms=lat_ms, status_code=202
                )
        else:
            item.state = "COMMITTED"  # restore candidate status if rejected
            await metrics.record_op(
                "rollback",
                success=False,
                latency_ms=lat_ms,
                status_code=resp.status_code,
            )
    except Exception as exc:
        lat_ms = (time.monotonic() - t0) * 1000
        item.state = "COMMITTED"
        logger.debug("ROLLBACK_OP_ERROR | seq=%d error=%s", item.seq, exc)
        await metrics.record_op(
            "rollback", success=False, latency_ms=lat_ms, status_code=0
        )


async def _op_purge(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    tenant_id: str,
    workspace_id: str,
    dataset_id: str,
    metrics: SoakMetrics,
    active_items: list[SoakItem],
) -> None:
    candidates = [it for it in active_items if it.state == "COMMITTED"]
    if len(candidates) <= 3:
        return

    item = candidates[0]
    active_items.remove(item)
    item.state = "PURGING"
    t0 = time.monotonic()
    try:
        resp = await client.delete(
            f"/v4/catalog/documents/{item.doc_id}",
            params={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "dataset_id": dataset_id,
            },
        )
        lat_ms = (time.monotonic() - t0) * 1000
        if resp.status_code == 202:
            await metrics.record_op(
                "purge", success=True, latency_ms=lat_ms, status_code=202
            )
            # Verify purged item is no longer returned as active search result with bounded retry
            verified_absent = False
            successful_verification_query = False
            found_active = False

            for _ in range(5):
                try:
                    s_resp = await client.post(
                        "/v4/memory/search",
                        json={
                            "session_id": session_id,
                            "dataset_ids": [dataset_id],
                            "query": item.subj,
                            "limit": 5,
                        },
                    )
                    if s_resp.status_code == 200:
                        successful_verification_query = True
                        results = s_resp.json().get("results", [])
                        if not _is_subject_in_search_results(item.subj, results):
                            verified_absent = True
                            found_active = False
                            break
                        found_active = True
                except Exception:
                    pass
                await asyncio.sleep(0.2)

            if verified_absent:
                item.state = "PURGED"
            elif successful_verification_query and found_active:
                item.state = "PURGED_BUT_VISIBLE"
                await metrics.record_correctness_violation("deleted_memory_visible")
            else:
                item.state = "PURGE_UNCONFIRMED"
                await metrics.record_correctness_violation("consistency_failures")
        else:
            item.state = "COMMITTED"
            active_items.append(item)
            await metrics.record_op(
                "purge",
                success=False,
                latency_ms=lat_ms,
                status_code=resp.status_code,
            )
    except Exception as exc:
        lat_ms = (time.monotonic() - t0) * 1000
        item.state = "COMMITTED"
        active_items.append(item)
        logger.debug("PURGE_OP_ERROR | seq=%d error=%s", item.seq, exc)
        await metrics.record_op(
            "purge", success=False, latency_ms=lat_ms, status_code=0
        )


async def _op_context(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    metrics: SoakMetrics,
) -> None:
    t0 = time.monotonic()
    try:
        resp = await client.get(
            f"/v4/sessions/{session_id}/context", params={"query": "SOAK"}
        )
        lat_ms = (time.monotonic() - t0) * 1000
        if resp.status_code == 200:
            await metrics.record_op(
                "context", success=True, latency_ms=lat_ms, status_code=200
            )
        else:
            await metrics.record_op(
                "context",
                success=False,
                latency_ms=lat_ms,
                status_code=resp.status_code,
            )
    except Exception as exc:
        lat_ms = (time.monotonic() - t0) * 1000
        logger.debug("CONTEXT_OP_ERROR | error=%s", exc)
        await metrics.record_op(
            "context", success=False, latency_ms=lat_ms, status_code=0
        )


async def _op_cross_scope_check(
    client: httpx.AsyncClient,
    *,
    session_a_id: str,
    dataset_a_id: str,
    session_b_id: str,
    dataset_b_id: str,
    seq: int,
    metrics: SoakMetrics,
) -> None:
    """Execute a real cross-scope isolation audit between Dataset A and Dataset B."""
    doc_id = f"cross-scope-doc-{seq:06d}"
    rev_id = f"cross-scope-rev-{seq:06d}-1"
    chunk_id = f"cross-scope-chk-{seq:06d}-1"
    subj = f"SOAK-SCOPEA-{seq:06d}"
    obj = f"CASE-SCOPEA-{seq:06d}"
    content = (
        f"{subj} is linked to {obj}. Scope A gizli veri kaydı "
        f"tarih={datetime.now(timezone.utc).isoformat()}."
    )

    payload = {
        "session_id": session_a_id,
        "dataset_id": dataset_a_id,
        "document_id": doc_id,
        "revision_id": rev_id,
        "chunk_id": chunk_id,
        "title": f"Cross-Scope Test #{seq}",
        "source_ref": "soak-cross-scope",
        "content": content,
        "evidence_span": f"0:{len(subj) + len(obj) + 14}",
        "revision_number": 1,
        "chunk_ordinal": 0,
        "finalize_revision": True,
        "metadata": {"soak_seq": seq, "op": "cross_scope"},
    }

    t0 = time.monotonic()
    try:
        resp = await client.post("/v4/memory/insert", json=payload)
        if resp.status_code != 202:
            lat_ms = (time.monotonic() - t0) * 1000
            await metrics.record_op(
                "cross_scope",
                success=False,
                latency_ms=lat_ms,
                status_code=resp.status_code,
            )
            return

        mutation_id = resp.json().get("mutation_id")
        if mutation_id:
            committed, _, _ = await _poll_mutation_committed(client, mutation_id)
            if not committed:
                await metrics.record_correctness_violation("missing_committed_memory")
                return

        # Query from Scope B: The memory MUST NOT be visible
        s_resp = await client.post(
            "/v4/memory/search",
            json={
                "session_id": session_b_id,
                "dataset_ids": [dataset_b_id],
                "query": subj,
                "limit": 5,
            },
        )
        lat_ms = (time.monotonic() - t0) * 1000
        if s_resp.status_code == 200:
            results = s_resp.json().get("results", [])
            leaked = _is_subject_in_search_results(subj, results)
            if leaked:
                logger.error(
                    "CROSS_SCOPE_LEAK_DETECTED | Item %s from Scope A found in Scope B search!",
                    subj,
                )
                await metrics.record_correctness_violation("cross_scope_results")
                await metrics.record_op(
                    "cross_scope", success=False, latency_ms=lat_ms, status_code=200
                )
            else:
                await metrics.record_op(
                    "cross_scope", success=True, latency_ms=lat_ms, status_code=200
                )
        else:
            await metrics.record_op(
                "cross_scope",
                success=False,
                latency_ms=lat_ms,
                status_code=s_resp.status_code,
            )
    except Exception as exc:
        lat_ms = (time.monotonic() - t0) * 1000
        logger.debug("CROSS_SCOPE_OP_ERROR | seq=%d error=%s", seq, exc)
        await metrics.record_op(
            "cross_scope", success=False, latency_ms=lat_ms, status_code=0
        )


async def load_driver_v4(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    dataset_id: str,
    session_b_id: str,
    dataset_b_id: str,
    tenant_id: str,
    workspace_id: str,
    rps: float,
    duration: float,
    metrics: SoakMetrics,
    stop_event: asyncio.Event,
) -> None:
    """Drive mixed V4 cognitive workload sustainably for configured duration."""
    active_items: list[SoakItem] = []
    interval = 1.0 / max(rps, 0.1)
    seq = 0
    t_start = time.monotonic()

    logger.info(
        "LOAD_DRIVER_V4 | Starting Profile A workload: %.1f op/s for %ds",
        rps,
        int(duration),
    )

    while not stop_event.is_set() and (time.monotonic() - t_start) < duration:
        roll = random.random()
        if roll < 0.30:
            seq += 1
            await _op_insert(
                client,
                session_id=session_id,
                dataset_id=dataset_id,
                seq=seq,
                metrics=metrics,
                active_items=active_items,
            )
        elif roll < 0.55:
            await _op_search(
                client,
                session_id=session_id,
                dataset_id=dataset_id,
                metrics=metrics,
                active_items=active_items,
            )
        elif roll < 0.65:
            await _op_revision(
                client,
                session_id=session_id,
                dataset_id=dataset_id,
                metrics=metrics,
                active_items=active_items,
            )
        elif roll < 0.75:
            await _op_idempotency(
                client,
                session_id=session_id,
                dataset_id=dataset_id,
                metrics=metrics,
                active_items=active_items,
            )
        elif roll < 0.82:
            await _op_rollback(
                client,
                metrics=metrics,
                active_items=active_items,
            )
        elif roll < 0.88:
            await _op_purge(
                client,
                session_id=session_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                metrics=metrics,
                active_items=active_items,
            )
        elif roll < 0.94:
            await _op_context(
                client,
                session_id=session_id,
                metrics=metrics,
            )
        else:
            seq += 1
            await _op_cross_scope_check(
                client,
                session_a_id=session_id,
                dataset_a_id=dataset_id,
                session_b_id=session_b_id,
                dataset_b_id=dataset_b_id,
                seq=seq,
                metrics=metrics,
            )

        await asyncio.sleep(interval)

    logger.info("LOAD_DRIVER_V4 | Finished workload after %d total items", seq)


# ---------------------------------------------------------------------------
# Telemetry collector & JSONL streaming
# ---------------------------------------------------------------------------


async def telemetry_collector_v4(
    client: httpx.AsyncClient,
    *,
    metrics: SoakMetrics,
    interval: float,
    log_file: Path,
    target_pid: int | None,
    is_embedded: bool,
    dao: Any | None,
    stop_event: asyncio.Event,
) -> None:
    """Collect system health, correctness, and resource telemetry periodically."""
    t_start = time.monotonic()
    tick = 0

    logger.info(
        "TELEMETRY_V4 | Starting collector (interval=%ds, log=%s)",
        int(interval),
        log_file,
    )

    while not stop_event.is_set():
        await asyncio.sleep(interval)
        tick += 1
        elapsed = time.monotonic() - t_start

        # Fail-safe health probe
        health_status = "unknown"
        is_healthy = False
        try:
            h_resp = await client.get("/health")
            if h_resp.status_code == 200:
                data = h_resp.json()
                status_val = data.get("status") if isinstance(data, dict) else None
                if status_val:
                    health_status = str(status_val)
                    is_healthy = health_status == "healthy"
                else:
                    health_status = "malformed_response"
            else:
                health_status = f"http_{h_resp.status_code}"
        except Exception as exc:
            health_status = f"error:{exc.__class__.__name__}"

        await metrics.record_health_check(is_healthy, health_status)

        # Query real runtime queues / backlogs
        runtime_state = await query_runtime_state(client, dao)
        await metrics.update_runtime_state(runtime_state)

        # Collect process resources
        resources = get_process_resources(target_pid, is_embedded=is_embedded)
        rss_mb = resources.get("rss_mb")
        if rss_mb is not None:
            if metrics.peak_rss_mb is None or rss_mb > metrics.peak_rss_mb:
                metrics.peak_rss_mb = rss_mb
        metrics.latest_resources = resources
        metrics.resource_samples.append(resources)

        snap = await metrics.snapshot()
        record = {
            "_type": "telemetry_tick",
            "tick": tick,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            "elapsed_hours": round(elapsed / 3600, 3),
            "health": health_status,
            "health_failures": metrics.health_checks_failed,
            "max_consecutive_health_failures": metrics.max_consecutive_health_failures,
            "total_operations": snap["total_operations"],
            "successful_operations": snap["successful_operations"],
            "failed_operations": snap["failed_operations"],
            "success_ratio": snap["success_ratio"],
            "counts": snap["counts"],
            "latencies": snap["latencies"],
            "correctness": snap["correctness"],
            "runtime": snap["runtime"],
            "resources": resources,
        }

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        correctness = snap["correctness"]
        counts = snap["counts"]
        logger.info(
            "TELEMETRY [%04d] | elapsed=%ds | ops=%d | ok=%d | fail=%d | "
            "ins=%d | srch=%d | rev=%d | purge=%d | cross=%d | "
            "wrong_ret=%d | miss_comm=%d | del_vis=%d | cross_leak=%d | "
            "health=%s | rss=%s",
            tick,
            int(elapsed),
            snap["total_operations"],
            snap["successful_operations"],
            snap["failed_operations"],
            counts["insert"],
            counts["search"],
            counts["revision"],
            counts["purge"],
            counts["cross_scope"],
            correctness["wrong_retrieval"],
            correctness["missing_committed_memory"],
            correctness["deleted_memory_visible"],
            correctness["cross_scope_results"],
            health_status,
            f"{rss_mb:.1f}MB" if rss_mb is not None else "unavailable",
        )


# ---------------------------------------------------------------------------
# Drain phase & Post-drain verifications
# ---------------------------------------------------------------------------


async def _drain_runtime(
    client: httpx.AsyncClient,
    dao: Any | None,
    *,
    timeout: float = DEFAULT_DRAIN_TIMEOUT,
    interval: float = 1.0,
) -> tuple[bool, int | None, bool]:
    """Wait for pending mutations and projection outbox backlogs to drain.

    Returns (drain_completed: bool, remaining_backlog: int | None, runtime_state_measurable: bool).
    """
    logger.info(
        "DRAIN | Waiting for in-flight work to drain (timeout=%.1fs)...", timeout
    )
    t0 = time.monotonic()
    deadline = t0 + timeout
    last_backlog: int | None = None
    state_measurable = False

    while time.monotonic() < deadline:
        state = await query_runtime_state(client, dao)
        pending_mut = state.get("pending_mutations")
        proj_backlog = state.get("projection_backlog")

        if pending_mut is None or proj_backlog is None:
            state_measurable = False
            last_backlog = None
        else:
            state_measurable = True
            total_pending = pending_mut + proj_backlog
            last_backlog = total_pending
            if total_pending == 0:
                logger.info(
                    "DRAIN | Drain completed cleanly in %.2fs", time.monotonic() - t0
                )
                return True, 0, True

        await asyncio.sleep(interval)

    if not state_measurable:
        logger.warning(
            "DRAIN | Runtime state metrics are unavailable during drain phase"
        )
        return False, None, False

    logger.warning(
        "DRAIN | Timeout after %.1fs with %s remaining pending work units",
        timeout,
        last_backlog,
    )
    return False, last_backlog, True


# ---------------------------------------------------------------------------
# Centralized Evaluation & Report Generation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SoakEvaluationResult:
    """Unified certification decision and exit code evaluation."""

    verdict: str  # "PROFILE A PASS", "PROFILE A FAIL", "SMOKE PASS — NOT CERTIFICATION", "SMOKE FAIL", "INTERRUPTED — NOT CERTIFIED", "PRE-FLIGHT FAILED"
    exit_code: int
    is_certification_eligible: bool
    reasons: list[str]
    critical_failures: dict[str, int]


def evaluate_profile_a_result(
    *,
    status: str,  # "ok", "pre_flight_failed", "session_start_failed", "interrupted"
    actual_elapsed_s: float,
    requested_duration_s: float,
    total_operations: int,
    successful_operations: int,
    failed_operations: int,
    counts: dict[str, int],
    correctness: dict[str, int],
    final_health: str,
    health_checks_total: int,
    health_checks_failed: int,
    max_consecutive_health_failures: int,
    resource_eval: dict[str, Any],
    drain_completed: bool,
    remaining_backlog: int | None,
    runtime_state_measurable: bool,
    runtime: dict[str, Any],
) -> SoakEvaluationResult:
    """Evaluate soak run against strict Profile A certification gates."""
    reasons: list[str] = []

    if status in {"pre_flight_failed", "session_start_failed"}:
        return SoakEvaluationResult(
            verdict="PRE-FLIGHT FAILED",
            exit_code=1,
            is_certification_eligible=False,
            reasons=[f"Pre-flight initialization failed (status: {status})"],
            critical_failures={},
        )

    if status == "interrupted":
        return SoakEvaluationResult(
            verdict="INTERRUPTED — NOT CERTIFIED",
            exit_code=130,
            is_certification_eligible=False,
            reasons=[
                "Soak execution was interrupted by user or signal before completion"
            ],
            critical_failures={},
        )

    is_cert_eligible = (
        actual_elapsed_s >= _MIN_PRODUCTION_DURATION
        and requested_duration_s >= _MIN_PRODUCTION_DURATION
    )

    # 1. Critical correctness checks (zero tolerance)
    crit_failures = {
        "wrong_retrieval": correctness.get("wrong_retrieval", 0),
        "missing_committed_memory": correctness.get("missing_committed_memory", 0),
        "deleted_memory_visible": correctness.get("deleted_memory_visible", 0),
        "cross_scope_results": correctness.get("cross_scope_results", 0),
        "consistency_failures": correctness.get("consistency_failures", 0),
    }
    dead_letters = runtime.get("dead_letters")
    if dead_letters is not None and dead_letters > 0:
        crit_failures["dead_letters"] = dead_letters

    failed_mutations = runtime.get("failed_mutations")
    if failed_mutations is not None and failed_mutations > 0:
        crit_failures["failed_mutations"] = failed_mutations

    pending_mutations = runtime.get("pending_mutations")
    if pending_mutations is not None and pending_mutations > 0:
        crit_failures["pending_mutations"] = pending_mutations

    projection_backlog = runtime.get("projection_backlog")
    if projection_backlog is not None and projection_backlog > 0:
        crit_failures["projection_backlog"] = projection_backlog

    for k, v in crit_failures.items():
        if v > 0:
            reasons.append(f"Critical correctness violation: {k}={v} (tolerance: 0)")

    # 2. Final health check
    if final_health != "healthy":
        reasons.append(f"Final runtime health is not healthy: {final_health}")

    # 3. Periodic health stability (tracks historical peak outage length)
    if max_consecutive_health_failures > MAX_CONSECUTIVE_HEALTH_FAILURES:
        reasons.append(
            f"Exceeded max consecutive health check failures: {max_consecutive_health_failures} > {MAX_CONSECUTIVE_HEALTH_FAILURES}"
        )
    if (
        health_checks_total > 0
        and (health_checks_failed / health_checks_total) > MAX_OPERATIONAL_FAILURE_RATE
    ):
        reasons.append(
            f"Health check failure rate ({health_checks_failed}/{health_checks_total}) exceeds 5%"
        )

    # 4. Operational failure rate
    if total_operations > 0:
        fail_rate = failed_operations / total_operations
        if fail_rate > MAX_OPERATIONAL_FAILURE_RATE:
            reasons.append(
                f"Operational failure rate {fail_rate:.2%} exceeds 5% threshold"
            )

    # 5. Drain phase check
    if not drain_completed or remaining_backlog != 0:
        reasons.append(
            f"Drain phase did not complete cleanly; remaining pending work: {remaining_backlog}"
        )

    # 6. Resource leak checks
    if resource_eval.get("possible_memory_leak"):
        reasons.append("Resource trend analysis detected possible memory leak")
    if resource_eval.get("thread_leak_detected"):
        reasons.append("Resource trend analysis detected thread leak")
    if resource_eval.get("fd_leak_detected"):
        reasons.append("Resource trend analysis detected file descriptor leak")

    # 7. For 24h certification: strict invariants
    if is_cert_eligible:
        if not runtime_state_measurable:
            reasons.append(
                "Runtime state queue/backlog metrics could not be verified from canonical storage"
            )
        if pending_mutations is None:
            reasons.append(
                "Final pending mutations metric is unavailable (tolerance: 0)"
            )
        if projection_backlog is None:
            reasons.append(
                "Final projection backlog metric is unavailable (tolerance: 0)"
            )
        if not resource_eval.get("evaluated", False):
            reasons.append(
                "Resource telemetry could not be evaluated (insufficient valid samples)"
            )
        required_ops = [
            "insert",
            "search",
            "revision",
            "idempotent_insert",
            "rollback",
            "purge",
            "context",
            "cross_scope",
        ]
        for op in required_ops:
            if counts.get(op, 0) == 0:
                reasons.append(f"Required lifecycle operation had 0 executions: {op}")

    has_failures = len(reasons) > 0

    if is_cert_eligible:
        if has_failures:
            verdict = "PROFILE A FAIL"
            exit_code = 1
        else:
            verdict = "PROFILE A PASS"
            exit_code = 0
    else:
        if has_failures:
            verdict = "SMOKE FAIL"
            exit_code = 1
        else:
            verdict = "SMOKE PASS — NOT CERTIFICATION"
            exit_code = 0

    return SoakEvaluationResult(
        verdict=verdict,
        exit_code=exit_code,
        is_certification_eligible=is_cert_eligible,
        reasons=reasons,
        critical_failures=crit_failures,
    )


def format_final_report(
    final_snap: dict[str, Any],
    eval_result: SoakEvaluationResult,
    *,
    mode_name: str,
    actual_elapsed_s: float,
    requested_duration_s: float,
    resource_eval: dict[str, Any],
    latest_resources: dict[str, Any],
    start_resources: dict[str, Any],
    peak_rss_mb: float | None,
    drain_completed: bool,
    remaining_backlog: int | None,
    runtime_state_measurable: bool,
) -> str:
    """Render the standard Profile A / Smoke Final Report."""
    correctness = final_snap.get("correctness", {})
    runtime = final_snap.get("runtime", {})
    counts = final_snap.get("counts", {})
    health = final_snap.get("health", {})

    def _val_or_unavail(val: Any) -> str:
        return str(val) if val is not None else "unavailable"

    def _res_mb(val: Any) -> str:
        return f"{val:.2f} MB" if val is not None else "unavailable"

    def _leak_str(flag: bool | None, evaluated: bool) -> str:
        if not evaluated:
            return "Inconclusive (insufficient samples)"
        return "Yes" if flag else "No"

    evaluated = resource_eval.get("evaluated", False)
    mem_leak_str = _leak_str(
        resource_eval.get("possible_memory_leak"), evaluated=evaluated
    )
    thread_leak_str = _leak_str(
        resource_eval.get("thread_leak_detected"), evaluated=evaluated
    )
    fd_leak_str = _leak_str(resource_eval.get("fd_leak_detected"), evaluated=evaluated)

    reasons_block = ""
    if eval_result.reasons:
        reasons_block = "\nReasons:\n" + "\n".join(
            f"  - {r}" for r in eval_result.reasons
        )

    return f"""
PROFILE A — SAFE CORE SOAK

Mode: {mode_name}
Duration: {int(actual_elapsed_s)}s ({actual_elapsed_s / 3600:.2f}h)
Requested duration: {int(requested_duration_s)}s
Certification eligible: {eval_result.is_certification_eligible}

Operations:
  Insert: {counts.get("insert", 0)}
  Search: {counts.get("search", 0)}
  Revision: {counts.get("revision", 0)}
  Idempotency: {counts.get("idempotent_insert", 0)}
  Rollback: {counts.get("rollback", 0)}
  Purge: {counts.get("purge", 0)}
  Context: {counts.get("context", 0)}
  Cross-scope: {counts.get("cross_scope", 0)}

Correctness:
  Wrong retrieval: {correctness.get("wrong_retrieval", 0)}
  Missing committed memory: {correctness.get("missing_committed_memory", 0)}
  Deleted memory visible: {correctness.get("deleted_memory_visible", 0)}
  Cross-scope results: {correctness.get("cross_scope_results", 0)}
  Consistency failures: {correctness.get("consistency_failures", 0)}

Runtime:
  Final health: {health.get("final_health", "unknown")}
  Health check failures: {health.get("health_checks_failed", 0)}/{health.get("health_checks_total", 0)}
  Max consecutive health failures: {health.get("max_consecutive_health_failures", 0)}
  Pending mutations: {_val_or_unavail(runtime.get("pending_mutations"))}
  Failed mutations: {_val_or_unavail(runtime.get("failed_mutations"))}
  Projection backlog: {_val_or_unavail(runtime.get("projection_backlog"))}
  Dead letters: {_val_or_unavail(runtime.get("dead_letters"))}
  Runtime state measurable: {runtime_state_measurable}

Resources:
  Start RSS: {_res_mb(start_resources.get("rss_mb"))}
  Final RSS: {_res_mb(latest_resources.get("rss_mb"))}
  Peak RSS: {_res_mb(peak_rss_mb)}
  Resource telemetry evaluated: {evaluated}
  Memory leak suspicion: {mem_leak_str}
  Threads: {_val_or_unavail(latest_resources.get("threads"))}
  Thread leak: {thread_leak_str}
  Open FDs: {_val_or_unavail(latest_resources.get("open_fds"))}
  FD leak: {fd_leak_str}

Drain:
  Completed: {drain_completed}
  Remaining work: {_val_or_unavail(remaining_backlog)}

RESULT:
{eval_result.verdict}{reasons_block}
""".strip()


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


async def _run_soak_on_client(
    client: httpx.AsyncClient,
    *,
    tenant_id: str,
    workspace_id: str,
    dataset_id: str,
    agent_id: str,
    duration: float,
    rps: float,
    telemetry_interval: float,
    drain_timeout: float,
    log_file: Path,
    target_pid: int | None,
    is_embedded: bool,
    mode_name: str,
    dao: Any | None = None,
    interrupted_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    metrics = SoakMetrics()
    stop_event = asyncio.Event()
    t_start = time.monotonic()

    # Pre-flight 1: Health check (fail-safe)
    logger.info("PRE-FLIGHT | Checking server readiness...")
    try:
        resp = await client.get("/health")
        if resp.status_code != 200:
            logger.error(
                "PRE-FLIGHT FAILED | Health check returned %d: %s",
                resp.status_code,
                resp.text,
            )
            return {
                "status": "pre_flight_failed",
                "http_status": resp.status_code,
                "error": f"Health check returned HTTP {resp.status_code}",
            }
        data = resp.json()
        status_val = data.get("status") if isinstance(data, dict) else None
        if status_val != "healthy":
            logger.error("PRE-FLIGHT FAILED | Status is not healthy: %s", status_val)
            return {
                "status": "pre_flight_failed",
                "http_status": resp.status_code,
                "error": f"Health status is not healthy ({status_val})",
            }
    except Exception as exc:
        logger.error("PRE-FLIGHT FAILED | Cannot reach server: %s", exc)
        return {"status": "pre_flight_failed", "error": str(exc)}

    # Pre-flight 2: Session A initialization
    logger.info(
        "SESSION_INIT | Starting Scope A session for tenant=%s dataset=%s agent=%s",
        tenant_id,
        dataset_id,
        agent_id,
    )
    s_resp_a = await client.post(
        "/v4/sessions/start",
        json={
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "dataset_ids": [dataset_id],
            "agent_id": agent_id,
        },
    )
    if s_resp_a.status_code != 201:
        logger.error(
            "SESSION_INIT FAILED (Scope A) | Returned %d: %s",
            s_resp_a.status_code,
            s_resp_a.text,
        )
        return {
            "status": "session_start_failed",
            "http_status": s_resp_a.status_code,
            "error": "Session A start failed",
        }
    session_id_a = s_resp_a.json()["session_id"]

    # Pre-flight 3: Session B initialization (for cross-scope isolation)
    dataset_b_id = f"{dataset_id}-b"
    agent_b_id = f"{agent_id}-b"
    logger.info(
        "SESSION_INIT | Starting Scope B session for tenant=%s dataset=%s agent=%s",
        tenant_id,
        dataset_b_id,
        agent_b_id,
    )
    s_resp_b = await client.post(
        "/v4/sessions/start",
        json={
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "dataset_ids": [dataset_b_id],
            "agent_id": agent_b_id,
        },
    )
    if s_resp_b.status_code != 201:
        logger.error(
            "SESSION_INIT FAILED (Scope B) | Returned %d: %s",
            s_resp_b.status_code,
            s_resp_b.text,
        )
        return {
            "status": "session_start_failed",
            "http_status": s_resp_b.status_code,
            "error": "Session B start failed",
        }
    session_id_b = s_resp_b.json()["session_id"]

    # Initial resources
    start_res = get_process_resources(target_pid, is_embedded=is_embedded)
    metrics.start_resources = start_res
    metrics.latest_resources = start_res
    metrics.peak_rss_mb = start_res.get("rss_mb")

    # Spawn driver and telemetry tasks
    driver_task = asyncio.create_task(
        load_driver_v4(
            client,
            session_id=session_id_a,
            dataset_id=dataset_id,
            session_b_id=session_id_b,
            dataset_b_id=dataset_b_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            rps=rps,
            duration=duration,
            metrics=metrics,
            stop_event=stop_event,
        )
    )
    telemetry_task = asyncio.create_task(
        telemetry_collector_v4(
            client,
            metrics=metrics,
            interval=telemetry_interval,
            log_file=log_file,
            target_pid=target_pid,
            is_embedded=is_embedded,
            dao=dao,
            stop_event=stop_event,
        )
    )

    interrupted = False

    async def _monitor_interruption() -> None:
        if interrupted_event is not None:
            await interrupted_event.wait()
            stop_event.set()

    monitor_task = (
        asyncio.create_task(_monitor_interruption())
        if interrupted_event is not None
        else None
    )

    try:
        await driver_task
    except asyncio.CancelledError:
        logger.warning("SOAK | Load driver cancelled")
        interrupted = True
    except KeyboardInterrupt:
        logger.warning("SOAK | Interrupted by user (Ctrl+C)")
        interrupted = True
    finally:
        if interrupted_event is not None and interrupted_event.is_set():
            interrupted = True
        if monitor_task is not None:
            monitor_task.cancel()
        stop_event.set()
        await asyncio.sleep(0.2)
        telemetry_task.cancel()
        try:
            await telemetry_task
        except asyncio.CancelledError:
            pass

    actual_elapsed_s = time.monotonic() - t_start

    # Post-workload final cross-scope isolation check (creates mutation, MUST run BEFORE drain)
    await _op_cross_scope_check(
        client,
        session_a_id=session_id_a,
        dataset_a_id=dataset_id,
        session_b_id=session_id_b,
        dataset_b_id=dataset_b_id,
        seq=999_999,
        metrics=metrics,
    )
    # NO FURTHER WORKLOAD MUTATIONS AFTER THIS POINT

    # Drain phase: Wait for ALL in-flight mutations and projection backlogs to settle to 0
    drain_completed, remaining_backlog, runtime_state_measurable = await _drain_runtime(
        client, dao, timeout=drain_timeout
    )

    # Post-drain verifications
    # 1. Final fail-safe health probe
    final_health = "unknown"
    try:
        h_resp = await client.get("/health")
        if h_resp.status_code == 200:
            data = h_resp.json()
            status_val = data.get("status") if isinstance(data, dict) else None
            final_health = str(status_val) if status_val else "malformed_response"
        else:
            final_health = f"http_{h_resp.status_code}"
    except Exception as exc:
        final_health = f"error:{exc.__class__.__name__}"
    metrics.final_health = final_health

    # 2. Final runtime queue state (queried strictly AFTER drain)
    final_runtime_state = await query_runtime_state(client, dao)
    await metrics.update_runtime_state(final_runtime_state)

    # 3. Final resource snapshot and trend evaluation
    final_res = get_process_resources(target_pid, is_embedded=is_embedded)
    final_rss = final_res.get("rss_mb")
    if final_rss is not None:
        if metrics.peak_rss_mb is None or final_rss > metrics.peak_rss_mb:
            metrics.peak_rss_mb = final_rss
    metrics.latest_resources = final_res
    resource_eval = evaluate_resource_trends(metrics.resource_samples)

    final_snap = await metrics.snapshot()
    status_label = "interrupted" if interrupted else "ok"

    eval_result = evaluate_profile_a_result(
        status=status_label,
        actual_elapsed_s=actual_elapsed_s,
        requested_duration_s=duration,
        total_operations=final_snap["total_operations"],
        successful_operations=final_snap["successful_operations"],
        failed_operations=final_snap["failed_operations"],
        counts=final_snap["counts"],
        correctness=final_snap["correctness"],
        final_health=final_health,
        health_checks_total=metrics.health_checks_total,
        health_checks_failed=metrics.health_checks_failed,
        max_consecutive_health_failures=metrics.max_consecutive_health_failures,
        resource_eval=resource_eval,
        drain_completed=drain_completed,
        remaining_backlog=remaining_backlog,
        runtime_state_measurable=runtime_state_measurable,
        runtime=final_snap["runtime"],
    )

    report_text = format_final_report(
        final_snap,
        eval_result,
        mode_name=mode_name,
        actual_elapsed_s=actual_elapsed_s,
        requested_duration_s=duration,
        resource_eval=resource_eval,
        latest_resources=final_res,
        start_resources=start_res,
        peak_rss_mb=metrics.peak_rss_mb,
        drain_completed=drain_completed,
        remaining_backlog=remaining_backlog,
        runtime_state_measurable=runtime_state_measurable,
    )

    final_snap["log_file"] = str(log_file)
    final_snap["mode"] = mode_name
    final_snap["actual_elapsed_s"] = round(actual_elapsed_s, 2)
    final_snap["duration_requested_s"] = duration
    final_snap["rps_target"] = rps
    final_snap["production_certification_duration_valid"] = (
        eval_result.is_certification_eligible
    )
    final_snap["resource_eval"] = resource_eval
    final_snap["start_resources"] = start_res
    final_snap["final_resources"] = final_res
    final_snap["peak_rss_mb"] = metrics.peak_rss_mb
    final_snap["drain_completed"] = drain_completed
    final_snap["remaining_backlog"] = remaining_backlog
    final_snap["runtime_state_measurable"] = runtime_state_measurable
    final_snap["evaluation"] = {
        "verdict": eval_result.verdict,
        "exit_code": eval_result.exit_code,
        "is_certification_eligible": eval_result.is_certification_eligible,
        "reasons": eval_result.reasons,
        "critical_failures": eval_result.critical_failures,
    }
    final_snap["report_text"] = report_text
    return final_snap


async def run_soak(
    *,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = "soak-test-key",
    duration: float = DEFAULT_DURATION,
    rps: float = DEFAULT_RPS,
    telemetry_interval: float = DEFAULT_TELEMETRY_INTERVAL,
    drain_timeout: float = DEFAULT_DRAIN_TIMEOUT,
    log_file: Path,
    server_pid: int | None = None,
    embedded: bool = False,
    storage_root: Path | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    dataset_id: str = DEFAULT_DATASET_ID,
    agent_id: str = DEFAULT_AGENT_ID,
    interrupted_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    """Execute the full Profile A soak test pipeline."""
    use_embedded = embedded
    if not use_embedded and base_url == DEFAULT_BASE_URL:
        # Probe local server fail-safe
        try:
            async with httpx.AsyncClient(timeout=2.0) as probe_client:
                r = await probe_client.get(f"{base_url}/health")
                if r.status_code != 200 or r.json().get("status") != "healthy":
                    use_embedded = True
        except Exception:
            use_embedded = True

    if use_embedded:
        mode_name = "Embedded V4 Combined Runtime (Real SQLite/LanceDB/Kuzu + Deterministic LLM)"
        logger.info("SOAK_MODE | Running %s", mode_name)
        s_root = (
            storage_root
            if storage_root
            else Path("./storage") / f"soak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        async with setup_soak_runtime(
            s_root,
            api_key=api_key or "soak-test-key",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            agent_id=agent_id,
        ) as (client, state):
            return await _run_soak_on_client(
                client,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                agent_id=agent_id,
                duration=duration,
                rps=rps,
                telemetry_interval=telemetry_interval,
                drain_timeout=drain_timeout,
                log_file=log_file,
                target_pid=server_pid or os.getpid(),
                is_embedded=True,
                mode_name=mode_name,
                dao=state.dao,
                interrupted_event=interrupted_event,
            )
    else:
        mode_name = f"Remote Server ({base_url})"
        logger.info("SOAK_MODE | Running against %s", mode_name)
        headers = {"X-API-Key": api_key}
        async with httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=30.0
        ) as client:
            return await _run_soak_on_client(
                client,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                agent_id=agent_id,
                duration=duration,
                rps=rps,
                telemetry_interval=telemetry_interval,
                drain_timeout=drain_timeout,
                log_file=log_file,
                target_pid=server_pid,
                is_embedded=False,
                mode_name=mode_name,
                dao=None,
                interrupted_event=interrupted_event,
            )


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MESA v0.7.1 — Profile A: Safe Core 24H Soak Test (Stability Certification)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=DEFAULT_BASE_URL,
        help=f"MESA API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.environ.get("MESA_API_KEY", "soak-test-key"),
        help="API key (default: $MESA_API_KEY or 'soak-test-key')",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION,
        help=f"Test duration in seconds (default: {DEFAULT_DURATION} = 24 hours)",
    )
    parser.add_argument(
        "--rps",
        type=float,
        default=DEFAULT_RPS,
        help=f"Target operations per second (default: {DEFAULT_RPS})",
    )
    parser.add_argument(
        "--telemetry-interval",
        type=float,
        default=DEFAULT_TELEMETRY_INTERVAL,
        help=f"Telemetry collection interval in seconds (default: {DEFAULT_TELEMETRY_INTERVAL})",
    )
    parser.add_argument(
        "--drain-timeout",
        type=float,
        default=DEFAULT_DRAIN_TIMEOUT,
        help=f"Drain timeout in seconds after workload completion (default: {DEFAULT_DRAIN_TIMEOUT})",
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default=".",
        help="Directory for telemetry log files (default: current directory)",
    )
    parser.add_argument(
        "--server-pid",
        type=int,
        default=None,
        help="Target MESA server process PID for remote resource monitoring (default: None)",
    )
    parser.add_argument(
        "--embedded",
        action="store_true",
        default=False,
        help="Force in-process embedded V4 combined runtime execution",
    )
    parser.add_argument(
        "--tenant-id",
        type=str,
        default=DEFAULT_TENANT_ID,
        help=f"V4 tenant ID (default: {DEFAULT_TENANT_ID})",
    )
    parser.add_argument(
        "--workspace-id",
        type=str,
        default=DEFAULT_WORKSPACE_ID,
        help=f"V4 workspace ID (default: {DEFAULT_WORKSPACE_ID})",
    )
    parser.add_argument(
        "--dataset-id",
        type=str,
        default=DEFAULT_DATASET_ID,
        help=f"V4 dataset ID (default: {DEFAULT_DATASET_ID})",
    )
    parser.add_argument(
        "--agent-id",
        type=str,
        default=DEFAULT_AGENT_ID,
        help=f"V4 agent ID (default: {DEFAULT_AGENT_ID})",
    )
    return parser


def main() -> None:
    """CLI entrypoint for Profile A Soak Test."""
    args = _build_parser().parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = Path(args.log_dir) / f"soak_test_{ts}.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "SOAK_CONFIG | duration=%ds rps=%.1f telemetry=%ds drain_timeout=%.1fs log=%s",
        args.duration,
        args.rps,
        int(args.telemetry_interval),
        args.drain_timeout,
        log_file,
    )

    if not is_production_certification_duration(args.duration):
        logger.warning(
            "⚠ DURATION_NOTICE | %ds < %ds (24h minimum). "
            "This run produces a SMOKE evaluation result. "
            "Full Profile A certification requires a complete 24-hour run.",
            args.duration,
            _MIN_PRODUCTION_DURATION,
        )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    interrupted_event = asyncio.Event()

    def _handle_signal(sig_name: str) -> None:
        logger.warning(
            "SOAK | Received %s signal — initiating graceful shutdown...", sig_name
        )
        interrupted_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig.name)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda s, f: _handle_signal(signal.Signals(s).name))

    try:
        final_report = loop.run_until_complete(
            run_soak(
                base_url=args.base_url,
                api_key=args.api_key,
                duration=args.duration,
                rps=args.rps,
                telemetry_interval=args.telemetry_interval,
                drain_timeout=args.drain_timeout,
                log_file=log_file,
                server_pid=args.server_pid,
                embedded=args.embedded,
                tenant_id=args.tenant_id,
                workspace_id=args.workspace_id,
                dataset_id=args.dataset_id,
                agent_id=args.agent_id,
                interrupted_event=interrupted_event,
            )
        )
    except KeyboardInterrupt:
        logger.warning("SOAK | Aborted by user (Ctrl+C)")
        sys.exit(130)
    finally:
        loop.close()

    # Print standard report
    report_text = final_report.get("report_text", "")
    print("\n" + "=" * 72)
    print(report_text)
    print("=" * 72)

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(
            json.dumps({"_type": "final_report", **final_report}, ensure_ascii=False)
            + "\n"
        )

    logger.info("SOAK | Telemetry log: %s", log_file)

    eval_data = final_report.get("evaluation", {})
    exit_code = eval_data.get("exit_code", 1)
    if exit_code != 0:
        logger.error(
            "SOAK_DECISION | Result: %s (exit code %d)",
            eval_data.get("verdict", "FAIL"),
            exit_code,
        )
    else:
        logger.info(
            "SOAK_DECISION | Result: %s (exit code %d)",
            eval_data.get("verdict", "PASS"),
            exit_code,
        )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
