"""MESA v0.7.1 — Profile A: Safe Core 24H Soak Test & Stability Certification.

Executes sustained real V4 cognitive lifecycle validation under moderate,
predictable load for up to 24 hours without external LLM/API dependencies.

Key Architecture:
    - Model/Embedding Boundary: Deterministic provider (zero remote LLM calls).
    - Runtime & Storage: Real FastAPI, real MESA workers, real SQLite, LanceDB,
      Kùzu graph, mutation ledger, outbox projections, RBAC, and ContextBuilder.
    - Lifecycle Operations: Insert, search recall verification, revisions,
      idempotency checks, rollbacks, document purges, and context retrieval.
    - Telemetry & Leaks: JSONL telemetry stream, psutil deep memory profiling
      (RSS, VMS, USS, threads, open FDs), and zero-tolerance correctness auditing.

.. warning::

    Runs under 24 hours (86,400 seconds) produce `SMOKE PASS — NOT CERTIFICATION`.
    Production certification strictly requires a completed 24-hour window with
    zero critical correctness violations.

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
DEFAULT_CONCURRENCY = 10
DEFAULT_MEMORY_PROFILE_INTERVAL = 1800.0  # 30 minutes
DEFAULT_TENANT_ID = "soak-tenant"
DEFAULT_WORKSPACE_ID = "default"
DEFAULT_DATASET_ID = "soak-dataset"
DEFAULT_AGENT_ID = "soak-agent"
DEFAULT_PRINCIPAL_ID = "soak-principal"

# ---------------------------------------------------------------------------
# Production certification threshold
# ---------------------------------------------------------------------------

_MIN_PRODUCTION_DURATION = 86_400  # 24 hours minimum for certification


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
                r"(SOAK-\d+)\s+is linked to\s+(CASE-\d+)", text, re.IGNORECASE
            )
            update_match = re.search(
                r"(SOAK-\d+)\s+is updated to\s+([A-Za-z0-9_\-]+)", text, re.IGNORECASE
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

    # Latencies in ms
    insert_latencies_ms: list[float] = field(default_factory=list)
    search_latencies_ms: list[float] = field(default_factory=list)
    commit_latencies_ms: list[float] = field(default_factory=list)

    # Correctness counters (zero tolerance in Profile A)
    wrong_retrieval_count: int = 0
    missing_committed_memory_count: int = 0
    deleted_memory_visible_count: int = 0
    cross_scope_result_count: int = 0
    consistency_failure_count: int = 0

    # Runtime queue / backlog stats
    pending_mutations: int = 0
    failed_mutations: int = 0
    dead_letters: int = 0
    projection_backlog: int = 0

    # Resource profiling
    resource_samples: list[dict[str, Any]] = field(default_factory=list)
    peak_rss_mb: float = 0.0
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
                self.http_errors[status_code] = (
                    self.http_errors.get(status_code, 0) + 1
                )

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

    async def record_commit(self, latency_ms: float) -> None:
        async with self._lock:
            self.commit_latencies_ms.append(latency_ms)

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
                },
                "latencies": {
                    "insert": _stats(self.insert_latencies_ms),
                    "search": _stats(self.search_latencies_ms),
                    "commit": _stats(self.commit_latencies_ms),
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
            }


# ---------------------------------------------------------------------------
# Resource profiling & Leak detection
# ---------------------------------------------------------------------------


def get_process_resources(pid: int | None = None) -> dict[str, Any]:
    """Retrieve process resource metrics (RSS, VMS, USS, threads, FDs)."""
    target_pid = pid if pid is not None else os.getpid()
    try:
        import psutil

        proc = psutil.Process(target_pid)
        mem = proc.memory_full_info()
        rss_mb = mem.rss / (1024 * 1024)
        vms_mb = mem.vms / (1024 * 1024)
        uss_mb = getattr(mem, "uss", mem.rss) / (1024 * 1024)
        num_fds = proc.num_fds() if hasattr(proc, "num_fds") else -1
        threads = proc.num_threads()
        cpu = proc.cpu_percent(interval=None)
        return {
            "status": "available",
            "pid": target_pid,
            "rss_mb": round(rss_mb, 2),
            "vms_mb": round(vms_mb, 2),
            "uss_mb": round(uss_mb, 2),
            "open_fds": num_fds,
            "threads": threads,
            "cpu_percent": round(cpu, 1),
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
                "vms_mb": -1.0,
                "uss_mb": -1.0,
                "open_fds": -1,
                "threads": -1,
                "cpu_percent": -1.0,
            }
        except Exception:
            return {"status": "unavailable", "pid": target_pid}
    except Exception as exc:
        return {"status": "unavailable", "pid": target_pid, "error": str(exc)}


def evaluate_resource_trends(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze periodic samples to detect memory, thread, or FD leak trends."""
    if not samples or len(samples) < 3:
        return {
            "possible_memory_leak": False,
            "thread_leak_detected": False,
            "fd_leak_detected": False,
        }

    valid_samples = [
        s
        for s in samples
        if s.get("status") in {"available", "partial_psutil_missing"}
    ]
    if len(valid_samples) < 3:
        return {
            "possible_memory_leak": False,
            "thread_leak_detected": False,
            "fd_leak_detected": False,
        }

    rss_values = [s["rss_mb"] for s in valid_samples if s.get("rss_mb", -1) > 0]
    fd_values = [s["open_fds"] for s in valid_samples if s.get("open_fds", -1) >= 0]
    thread_values = [s["threads"] for s in valid_samples if s.get("threads", -1) > 0]

    possible_memory_leak = False
    if len(rss_values) >= 4:
        start_rss = rss_values[0]
        final_rss = rss_values[-1]
        growth = final_rss - start_rss
        recent_rss = rss_values[-4:]
        is_monotonic = all(
            recent_rss[i] <= recent_rss[i + 1]
            for i in range(len(recent_rss) - 1)
        )
        if growth > 50.0 and final_rss > start_rss * 1.5 and is_monotonic:
            possible_memory_leak = True

    fd_leak = False
    if len(fd_values) >= 4 and (fd_values[-1] - fd_values[0]) > 50:
        fd_leak = True

    thread_leak = False
    if len(thread_values) >= 4 and (thread_values[-1] - thread_values[0]) > 20:
        thread_leak = True

    return {
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

    storage_root.mkdir(parents=True, exist_ok=True)
    os.environ["MESA_RUNTIME_PROFILE"] = "combined"
    os.environ["MESA_STORAGE_ROOT"] = str(storage_root)
    os.environ["MESA_LOAD_DOTENV"] = "false"
    os.environ["MESA_MODEL_ENABLED"] = "true"
    os.environ["MESA_EXTERNAL_PROVIDER_ENABLED"] = "true"
    os.environ["MESA_TIER3_MODE"] = "0"
    os.environ["MESA_REBEL_ENABLED"] = "false"
    os.environ["MESA_EMBEDDING_DIMENSION"] = "384"
    os.environ["MESA_LLM_PROVIDER"] = "mock"
    os.environ["MESA_API_KEY"] = api_key
    os.environ["MESA_PRINCIPAL_ID"] = principal_id
    os.environ["MESA_PRINCIPAL_STATUS"] = "active"

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

            # Bootstrap catalog and RBAC authorizations
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
        server.AdapterFactory.get_adapter = orig_get_adapter
        server._get_embedding_service = orig_embedding_service
        if orig_pipeline is not None:
            rebel_pipeline.pipeline = orig_pipeline


# ---------------------------------------------------------------------------
# Workload driver & Lifecycle operations
# ---------------------------------------------------------------------------


async def _poll_mutation_committed(
    client: httpx.AsyncClient,
    mutation_id: str,
    *,
    timeout: float = 20.0,
) -> tuple[bool, float, dict[str, Any]]:
    """Poll GET /v4/mutations/{mutation_id} until COMMITTED or timeout."""
    t0 = time.monotonic()
    deadline = t0 + timeout
    while time.monotonic() < deadline:
        try:
            resp = await client.get(f"/v4/mutations/{mutation_id}")
            if resp.status_code == 200:
                data = resp.json()
                state = data.get("state")
                if state == "COMMITTED":
                    latency_ms = (time.monotonic() - t0) * 1000
                    return True, latency_ms, data
                if state in {"FAILED", "REJECTED"}:
                    return False, (time.monotonic() - t0) * 1000, data
        except Exception:
            pass
        await asyncio.sleep(0.1)
    return False, (time.monotonic() - t0) * 1000, {"state": "TIMEOUT"}


def _is_subject_in_search_results(expected_subj: str, results: list[dict[str, Any]]) -> bool:
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
            if (
                expected_subj in str(prov.get("fact_text", ""))
                or expected_subj in str(prov.get("predicate", ""))
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
    content = f"{subj} is linked to {obj}. Turkish legal context and evidence at {datetime.now(timezone.utc).isoformat()}."
    idemp_key = f"idemp-soak-{seq:06d}"

    payload = {
        "session_id": session_id,
        "dataset_id": dataset_id,
        "document_id": doc_id,
        "revision_id": rev_id,
        "chunk_id": chunk_id,
        "title": f"Soak Entry #{seq}",
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

        committed, commit_lat, _ = await _poll_mutation_committed(
            client, mutation_id
        )
        if not committed:
            await metrics.record_correctness_violation("missing_committed_memory")
            return

        await metrics.record_commit(commit_lat)

        # Immediate verification search
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
            if not _is_subject_in_search_results(subj, results):
                await metrics.record_correctness_violation("missing_committed_memory")
                await metrics.record_correctness_violation("wrong_retrieval")
        else:
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
    if not active_items:
        query = "soak"
        expected_subj = None
    else:
        target = random.choice(active_items)
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
    candidates = [it for it in active_items if it.revision_number == 1]
    if not candidates:
        return

    item = random.choice(candidates)
    new_rev_id = f"soak-rev-{item.seq:06d}-2"
    new_chunk_id = f"soak-chk-{item.seq:06d}-2"
    new_obj = f"STATUS-RESOLVED-{item.seq:06d}"
    new_content = f"{item.subj} is updated to {new_obj}. Revision 2 recorded at {datetime.now(timezone.utc).isoformat()}."

    payload = {
        "session_id": session_id,
        "dataset_id": dataset_id,
        "document_id": item.doc_id,
        "revision_id": new_rev_id,
        "chunk_id": new_chunk_id,
        "title": f"Soak Entry #{item.seq} (Rev 2)",
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
                "revision", success=False, latency_ms=lat_ms, status_code=resp.status_code
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
        if it.idempotency_key is not None and it.revision_number == 1
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
        "title": f"Soak Entry #{item.seq}",
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
    candidates = [it for it in active_items if it.revision_number == 2]
    if not candidates:
        return

    item = random.choice(candidates)
    t0 = time.monotonic()
    try:
        resp = await client.post(f"/v4/mutations/{item.mutation_id}/rollback")
        lat_ms = (time.monotonic() - t0) * 1000
        if resp.status_code == 202:
            item.state = "ROLLED_BACK"
            await metrics.record_op(
                "rollback", success=True, latency_ms=lat_ms, status_code=202
            )
        else:
            await metrics.record_op(
                "rollback",
                success=False,
                latency_ms=lat_ms,
                status_code=resp.status_code,
            )
    except Exception as exc:
        lat_ms = (time.monotonic() - t0) * 1000
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
    if len(active_items) <= 3:
        return

    item = active_items.pop(0)
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
            # Verify purged item is no longer returned as active search result
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
                results = s_resp.json().get("results", [])
                found_active = any(
                    r.get("subject_name") == item.subj
                    for r in results
                    if r.get("status") == "ACTIVE"
                )
                if found_active:
                    await metrics.record_correctness_violation(
                        "deleted_memory_visible"
                    )
        else:
            await metrics.record_op(
                "purge",
                success=False,
                latency_ms=lat_ms,
                status_code=resp.status_code,
            )
    except Exception as exc:
        lat_ms = (time.monotonic() - t0) * 1000
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


async def load_driver_v4(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    tenant_id: str,
    workspace_id: str,
    dataset_id: str,
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
        if roll < 0.35:
            seq += 1
            await _op_insert(
                client,
                session_id=session_id,
                dataset_id=dataset_id,
                seq=seq,
                metrics=metrics,
                active_items=active_items,
            )
        elif roll < 0.70:
            await _op_search(
                client,
                session_id=session_id,
                dataset_id=dataset_id,
                metrics=metrics,
                active_items=active_items,
            )
        elif roll < 0.80:
            await _op_revision(
                client,
                session_id=session_id,
                dataset_id=dataset_id,
                metrics=metrics,
                active_items=active_items,
            )
        elif roll < 0.88:
            await _op_idempotency(
                client,
                session_id=session_id,
                dataset_id=dataset_id,
                metrics=metrics,
                active_items=active_items,
            )
        elif roll < 0.93:
            await _op_rollback(
                client,
                metrics=metrics,
                active_items=active_items,
            )
        elif roll < 0.97:
            await _op_purge(
                client,
                session_id=session_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                metrics=metrics,
                active_items=active_items,
            )
        else:
            await _op_context(
                client,
                session_id=session_id,
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

        health_status = "unknown"
        try:
            h_resp = await client.get("/health")
            if h_resp.status_code == 200:
                health_status = h_resp.json().get("status", "healthy")
            else:
                health_status = f"http_{h_resp.status_code}"
        except Exception as exc:
            health_status = f"error:{exc.__class__.__name__}"

        resources = get_process_resources(target_pid)
        rss_mb = resources.get("rss_mb", -1.0)
        if rss_mb > metrics.peak_rss_mb:
            metrics.peak_rss_mb = rss_mb
        metrics.latest_resources = resources
        metrics.resource_samples.append(resources)

        snap = await metrics.snapshot()
        record = {
            "_type": "telemetry_tick",
            "tick": tick,
            "elapsed_s": round(elapsed, 1),
            "elapsed_hours": round(elapsed / 3600, 3),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "health": health_status,
            "resources": resources,
            **snap,
        }

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        correctness = snap["correctness"]
        counts = snap["counts"]
        logger.info(
            "TELEMETRY [%04d] | elapsed=%ds | ops=%d | ok=%d | fail=%d | "
            "ins=%d | srch=%d | rev=%d | purge=%d | "
            "wrong_ret=%d | miss_comm=%d | del_vis=%d | "
            "health=%s | rss=%.1fMB",
            tick,
            int(elapsed),
            snap["total_operations"],
            snap["successful_operations"],
            snap["failed_operations"],
            counts["insert"],
            counts["search"],
            counts["revision"],
            counts["purge"],
            correctness["wrong_retrieval"],
            correctness["missing_committed_memory"],
            correctness["deleted_memory_visible"],
            health_status,
            rss_mb,
        )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def format_final_report(
    final_snap: dict[str, Any],
    duration: float,
    resource_eval: dict[str, Any],
    latest_resources: dict[str, Any],
    start_resources: dict[str, Any],
    peak_rss_mb: float,
) -> str:
    """Render the standard Profile A / Smoke Final Report."""
    is_cert_duration = is_production_certification_duration(duration)
    correctness = final_snap.get("correctness", {})
    runtime = final_snap.get("runtime", {})
    counts = final_snap.get("counts", {})

    has_critical_failure = (
        correctness.get("wrong_retrieval", 0) > 0
        or correctness.get("missing_committed_memory", 0) > 0
        or correctness.get("deleted_memory_visible", 0) > 0
        or correctness.get("cross_scope_results", 0) > 0
        or correctness.get("consistency_failures", 0) > 0
        or runtime.get("failed_mutations", 0) > 0
        or runtime.get("dead_letters", 0) > 0
    )

    total_ops = final_snap.get("total_operations", 0)
    failed_ops = final_snap.get("failed_operations", 0)
    high_failure_rate = total_ops > 0 and (failed_ops / total_ops) > 0.05

    if has_critical_failure or high_failure_rate:
        result = "PROFILE A FAIL" if is_cert_duration else "SMOKE FAIL"
    else:
        result = (
            "PROFILE A PASS"
            if is_cert_duration
            else "SMOKE PASS — NOT CERTIFICATION"
        )

    start_rss = start_resources.get("rss_mb", -1)
    final_rss = latest_resources.get("rss_mb", -1)
    cpu = latest_resources.get("cpu_percent", -1)
    threads = latest_resources.get("threads", -1)
    fds = latest_resources.get("open_fds", -1)

    return f"""
PROFILE A — SAFE CORE SOAK

Duration: {int(duration)}s ({duration / 3600:.2f}h)
Certification duration valid: {is_cert_duration}

Operations:
  Insert: {counts.get("insert", 0)}
  Search: {counts.get("search", 0)}
  Revision: {counts.get("revision", 0)}
  Rollback: {counts.get("rollback", 0)}
  Purge: {counts.get("purge", 0)}

Correctness:
  Wrong retrieval: {correctness.get("wrong_retrieval", 0)}
  Missing committed memory: {correctness.get("missing_committed_memory", 0)}
  Deleted memory visible: {correctness.get("deleted_memory_visible", 0)}
  Cross-scope results: {correctness.get("cross_scope_results", 0)}
  Consistency failures: {correctness.get("consistency_failures", 0)}

Runtime:
  Health: {final_snap.get("health", "healthy")}
  Pending mutations: {runtime.get("pending_mutations", 0)}
  Failed mutations: {runtime.get("failed_mutations", 0)}
  Dead letters: {runtime.get("dead_letters", 0)}

Resources:
  Start RAM: {start_rss} MB
  Final RAM: {final_rss} MB
  Peak RAM: {peak_rss_mb:.2f} MB
  CPU: {cpu}%
  Threads: {threads}
  Open FDs: {fds}

RESULT:
{result}
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
    log_file: Path,
    target_pid: int | None,
) -> dict[str, Any]:
    metrics = SoakMetrics()
    stop_event = asyncio.Event()

    # Pre-flight health check
    logger.info("PRE-FLIGHT | Checking server readiness...")
    try:
        resp = await client.get("/health")
        if resp.status_code != 200:
            logger.error("PRE-FLIGHT FAILED | Health check returned %d", resp.status_code)
            return {"status": "pre_flight_failed", "http_status": resp.status_code}
    except Exception as exc:
        logger.error("PRE-FLIGHT FAILED | Cannot reach server: %s", exc)
        return {"status": "pre_flight_failed", "error": str(exc)}

    # Session start
    logger.info(
        "SESSION_INIT | Starting V4 session for tenant=%s dataset=%s agent=%s",
        tenant_id,
        dataset_id,
        agent_id,
    )
    s_resp = await client.post(
        "/v4/sessions/start",
        json={
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "dataset_ids": [dataset_id],
            "agent_id": agent_id,
        },
    )
    if s_resp.status_code != 201:
        logger.error("SESSION_INIT FAILED | Returned %d: %s", s_resp.status_code, s_resp.text)
        return {"status": "session_start_failed", "http_status": s_resp.status_code}

    session_id = s_resp.json()["session_id"]
    logger.info("SESSION_INIT | Session active: %s", session_id)

    # Initial resources
    start_res = get_process_resources(target_pid)
    metrics.start_resources = start_res
    metrics.latest_resources = start_res
    metrics.peak_rss_mb = start_res.get("rss_mb", 0.0)

    # Spawn driver and telemetry tasks
    driver_task = asyncio.create_task(
        load_driver_v4(
            client,
            session_id=session_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            dataset_id=dataset_id,
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
            stop_event=stop_event,
        )
    )

    try:
        await driver_task
    except asyncio.CancelledError:
        logger.warning("SOAK | Load driver cancelled")
    except KeyboardInterrupt:
        logger.warning("SOAK | Interrupted by user")
    finally:
        stop_event.set()
        await asyncio.sleep(0.5)
        telemetry_task.cancel()
        try:
            await telemetry_task
        except asyncio.CancelledError:
            pass

    final_snap = await metrics.snapshot()
    final_res = get_process_resources(target_pid)
    resource_eval = evaluate_resource_trends(metrics.resource_samples)

    final_snap["log_file"] = str(log_file)
    final_snap["duration_requested_s"] = duration
    final_snap["rps_target"] = rps
    final_snap["production_certification_duration_valid"] = (
        is_production_certification_duration(duration)
    )
    final_snap["resource_eval"] = resource_eval
    final_snap["start_resources"] = start_res
    final_snap["final_resources"] = final_res
    final_snap["peak_rss_mb"] = metrics.peak_rss_mb

    report_text = format_final_report(
        final_snap,
        duration,
        resource_eval,
        final_res,
        start_res,
        metrics.peak_rss_mb,
    )
    final_snap["report_text"] = report_text
    return final_snap


async def run_soak(
    *,
    base_url: str,
    api_key: str,
    duration: float,
    rps: float,
    concurrency: int,
    telemetry_interval: float,
    log_file: Path,
    server_pid: int | None = None,
    embedded: bool = False,
    storage_root: Path | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    dataset_id: str = DEFAULT_DATASET_ID,
    agent_id: str = DEFAULT_AGENT_ID,
) -> dict[str, Any]:
    """Execute the full Profile A soak test pipeline."""
    # Determine execution mode: embedded runtime vs remote HTTP
    use_embedded = embedded
    if not use_embedded and base_url == DEFAULT_BASE_URL:
        # Check if local server is already running
        try:
            async with httpx.AsyncClient(timeout=2.0) as probe_client:
                r = await probe_client.get(f"{base_url}/health")
                if r.status_code != 200:
                    use_embedded = True
        except Exception:
            use_embedded = True

    if use_embedded:
        logger.info(
            "SOAK_MODE | Running embedded V4 runtime in-process (deterministic provider)"
        )
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
        ) as (client, _state):
            return await _run_soak_on_client(
                client,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                agent_id=agent_id,
                duration=duration,
                rps=rps,
                telemetry_interval=telemetry_interval,
                log_file=log_file,
                target_pid=server_pid or os.getpid(),
            )
    else:
        logger.info("SOAK_MODE | Running against remote server at %s", base_url)
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
                log_file=log_file,
                target_pid=server_pid,
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
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Max concurrent connections (default: {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--telemetry-interval",
        type=float,
        default=DEFAULT_TELEMETRY_INTERVAL,
        help=f"Telemetry collection interval in seconds (default: {DEFAULT_TELEMETRY_INTERVAL})",
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
        help="Target MESA server process PID for resource monitoring (default: in-process PID)",
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
        "SOAK_CONFIG | duration=%ds rps=%.1f telemetry=%ds log=%s",
        args.duration,
        args.rps,
        int(args.telemetry_interval),
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

    try:
        final_report = asyncio.run(
            run_soak(
                base_url=args.base_url,
                api_key=args.api_key,
                duration=args.duration,
                rps=args.rps,
                concurrency=args.concurrency,
                telemetry_interval=args.telemetry_interval,
                log_file=log_file,
                server_pid=args.server_pid,
                embedded=args.embedded,
                tenant_id=args.tenant_id,
                workspace_id=args.workspace_id,
                dataset_id=args.dataset_id,
                agent_id=args.agent_id,
            )
        )
    except KeyboardInterrupt:
        logger.warning("SOAK | Aborted by user (Ctrl+C)")
        sys.exit(130)

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

    # Fail closed on critical correctness issues or failure ratio > 5%
    correctness = final_report.get("correctness", {})
    critical_errors = (
        correctness.get("wrong_retrieval", 0)
        + correctness.get("missing_committed_memory", 0)
        + correctness.get("deleted_memory_visible", 0)
        + correctness.get("cross_scope_results", 0)
        + correctness.get("consistency_failures", 0)
    )

    total_ops = final_report.get("total_operations", 0)
    failed_ops = final_report.get("failed_operations", 0)

    if critical_errors > 0:
        logger.error(
            "SOAK_FAIL | %d critical correctness violations detected", critical_errors
        )
        sys.exit(1)

    if total_ops > 0 and (failed_ops / total_ops) > 0.05:
        logger.error(
            "SOAK_FAIL | Failure ratio %.2f%% exceeds 5%% threshold",
            (failed_ops / total_ops) * 100,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
