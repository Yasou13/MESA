from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import httpx

from ..core.suite import sync_target
from .catalog import dataset_detail, dataset_spec
from .models import PlanRequest
from .planner import materialize_plan
from .registry import JobRegistry

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|password|authorization)(\s*[=:]\s*)([^\s,;]+)"),
    re.compile(r"(https?://)([^/@\s:]+):([^/@\s]+)@"),
)


class JobManager:
    """Owns sequential benchmark subprocesses and cooperative controls."""

    def __init__(self, registry: JobRegistry, results_root: Path) -> None:
        self.registry = registry
        self.results_root = results_root
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._dataset_thread: threading.Thread | None = None
        self._reconcile()
        self._scheduler = threading.Thread(
            target=self._scheduler_loop,
            name="benchmark-dashboard-scheduler",
            daemon=True,
        )
        self._scheduler.start()

    def _reconcile(self) -> None:
        for job in self.registry.list(include_archived=True):
            if job.status == "running":
                self.registry.update(
                    job.id,
                    status="paused",
                    pid=None,
                    pause_reason="dashboard_restart",
                    error="Dashboard yeniden başlatıldı; çalışma güvenli biçimde devam ettirilebilir.",
                )
        for operation in self.registry.list_dataset_operations():
            if operation.status in {"queued", "running"}:
                self.registry.update_dataset_operation(
                    operation.id,
                    status="failed",
                    progress=100.0,
                    error=(
                        "Dashboard yeniden başlatıldığı için sync durumu doğrulanamadı; "
                        "işlemi yeniden başlatın."
                    ),
                )
        self._refresh_queue_positions()

    def create(self, request: PlanRequest) -> dict[str, Any]:
        ollama = self.active_ollama()
        seconds_per_question, history_samples = self.runtime_estimate(request)
        plan = materialize_plan(
            request,
            results_root=self.results_root,
            ollama_configured=bool(ollama.get("url")),
            default_model=ollama.get("model"),
            seconds_per_question=seconds_per_question,
            history_samples=history_samples,
        )
        job = self.registry.create(plan)
        with self._condition:
            self._refresh_queue_positions()
            self._condition.notify_all()
        current = self.registry.get(job.id)
        assert current is not None
        return current.model_dump()

    def active_ollama(self) -> dict[str, Any]:
        saved = self.registry.get_setting("ollama")
        if saved:
            return saved
        return {
            "url": os.environ.get("BENCHMARK_OLLAMA_URL"),
            "model": os.environ.get("BENCHMARK_GENERATOR_MODEL"),
            "source": "environment",
        }

    @staticmethod
    def _runtime_key(profile: str, config: str) -> str:
        return f"{profile}|{config}"

    def runtime_estimate(self, request: PlanRequest) -> tuple[float | None, int]:
        history = self.registry.get_setting("runtime_history") or {}
        value = history.get(self._runtime_key(request.profile, request.config))
        if not isinstance(value, dict):
            return None, 0
        seconds = value.get("seconds_per_question")
        samples = int(value.get("samples") or 0)
        return (float(seconds), samples) if seconds else (None, samples)

    def _record_runtime(self, plan: dict[str, Any], elapsed_seconds: float) -> None:
        question_tasks = (
            sum(int(shard["questions"]) for shard in plan["shards"])
            * len(plan["clients"])
            * int(plan["request"].get("iterations", 1))
        )
        if question_tasks <= 0:
            return
        sample = elapsed_seconds / question_tasks
        history = self.registry.get_setting("runtime_history") or {}
        key = self._runtime_key(plan["profile"], plan["request"]["config"])
        previous = history.get(key) or {}
        samples = int(previous.get("samples") or 0)
        previous_average = float(previous.get("seconds_per_question") or sample)
        history[key] = {
            "seconds_per_question": (previous_average * samples + sample)
            / (samples + 1),
            "samples": samples + 1,
        }
        self.registry.set_setting("runtime_history", history)

    def _refresh_queue_positions(self) -> None:
        queued = [
            item
            for item in reversed(self.registry.list(include_archived=True))
            if item.status == "queued"
        ]
        for index, item in enumerate(queued, start=1):
            if item.queue_position != index:
                self.registry.update(item.id, queue_position=index)

    def _scheduler_loop(self) -> None:
        while True:
            with self._condition:
                queued = [
                    item
                    for item in reversed(self.registry.list(include_archived=True))
                    if item.status == "queued"
                ]
                if not queued:
                    self._condition.wait(timeout=1.0)
                    continue
                job_id = queued[0].id
            self._run_job(job_id)
            with self._condition:
                self._refresh_queue_positions()

    @staticmethod
    def _load_plan(path: str | Path) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(Path(path).read_text(encoding="utf-8")))

    @staticmethod
    def _save_plan(path: str | Path, plan: dict[str, Any]) -> None:
        target = Path(path)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)

    @staticmethod
    def _append_event(path: str | Path, event: dict[str, Any]) -> None:
        value = {
            "schema_version": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _run_job(self, job_id: str) -> None:
        job = self.registry.get(job_id)
        if job is None:
            return
        plan = self._load_plan(job.plan_path)
        tasks = plan["tasks"]
        control_path = Path(job.plan_path).parent / "control.json"
        control_path.write_text("{}\n", encoding="utf-8")
        completed = sum(task["status"] == "completed" for task in tasks)
        warmed_clients: set[str] = set()
        active_started = time.monotonic()
        self.registry.update(
            job_id,
            status="running",
            progress=(
                max(job.progress, completed / len(tasks) * 98.0)
                if tasks
                else job.progress
            ),
            error=None,
            queue_position=None,
            started_at=job.started_at or datetime.now(timezone.utc).isoformat(),
            pause_reason=None,
        )
        started = time.monotonic()
        try:
            for task_index, task in enumerate(tasks):
                if task["status"] == "completed":
                    continue
                if task["status"] == "cancelled":
                    continue
                task["status"] = "running"
                self._save_plan(job.plan_path, plan)
                self.registry.update(
                    job_id,
                    current_task=task["id"],
                    progress=max(
                        (self.registry.get(job_id) or job).progress,
                        completed / len(tasks) * 98.0,
                    ),
                )
                self._append_event(
                    job.event_path,
                    {
                        "run_id": job_id,
                        "phase": "setup",
                        "status": "task_started",
                        "message": task["id"],
                        "metadata": {
                            "task_index": task_index + 1,
                            "task_total": len(tasks),
                            "client": task["client"],
                            "shard_id": task["shard_id"],
                        },
                    },
                )
                if plan.get("warmup_enabled") and task["client"] not in warmed_clients:
                    self._warmup_ollama(job_id, job.event_path, task["client"])
                    warmed_clients.add(task["client"])
                environment = os.environ.copy()
                environment["MESA_BENCHMARK_EVENT_FILE"] = job.event_path
                environment["MESA_BENCHMARK_CONTROL_FILE"] = str(control_path)
                ollama = self.active_ollama()
                if ollama.get("url"):
                    environment["BENCHMARK_OLLAMA_URL"] = str(ollama["url"])
                if ollama.get("model"):
                    environment["BENCHMARK_GENERATOR_MODEL"] = str(ollama["model"])
                command = [
                    sys.executable,
                    "-m",
                    "mesa_benchmark.cli",
                    "run",
                    "--config",
                    task["config"],
                    "--results-root",
                    task["results_root"],
                ]
                event_offset = len(
                    Path(job.event_path).read_text(encoding="utf-8").splitlines()
                )
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=environment,
                    cwd=str(Path.cwd()),
                )
                with self._lock:
                    self._processes[job_id] = process
                self.registry.update(job_id, pid=process.pid)
                limit_requested = False
                while process.poll() is None:
                    elapsed_active = job.active_elapsed_seconds + (
                        time.monotonic() - active_started
                    )
                    limit = self.registry.get(job_id)
                    if (
                        limit
                        and limit.time_limit_minutes is not None
                        and elapsed_active >= limit.time_limit_minutes * 60.0
                        and not limit_requested
                    ):
                        control_path.write_text(
                            json.dumps(
                                {
                                    "action": "pause",
                                    "reason": "time_limit",
                                    "requested_at": time.time(),
                                }
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                        limit_requested = True
                        self._append_event(
                            job.event_path,
                            {
                                "run_id": job_id,
                                "phase": "control",
                                "status": "requested",
                                "message": "Süre limiti; mevcut soru tamamlanıyor",
                            },
                        )
                    self._monitor_task(
                        job_id,
                        job.event_path,
                        plan,
                        task,
                        completed,
                        event_offset,
                        elapsed_active,
                    )
                    time.sleep(0.5)
                stdout, stderr = process.communicate()
                log_root = Path(job.plan_path).parent / "logs"
                log_root.mkdir(exist_ok=True)
                attempt = int(task.get("attempt", 1))
                stdout_log = log_root / f"{task['id']}-attempt-{attempt}.stdout.log"
                stderr_log = log_root / f"{task['id']}-attempt-{attempt}.stderr.log"
                stdout_log.write_text(stdout, encoding="utf-8")
                stderr_log.write_text(stderr, encoding="utf-8")
                task["stdout_log"] = str(stdout_log)
                task["stderr_log"] = str(stderr_log)
                with self._lock:
                    self._processes.pop(job_id, None)
                self.registry.update(job_id, pid=None)
                outcome: dict[str, Any] = {}
                if stdout.strip():
                    try:
                        outcome = json.loads(stdout)
                    except json.JSONDecodeError:
                        outcome = {}
                state = outcome.get("status")
                if state == "paused":
                    task["status"] = "paused"
                    task["outcome"] = outcome
                    self._save_plan(job.plan_path, plan)
                    elapsed_active = job.active_elapsed_seconds + (
                        time.monotonic() - active_started
                    )
                    self.registry.update(
                        job_id,
                        status="paused",
                        active_elapsed_seconds=elapsed_active,
                        pause_reason="time_limit" if limit_requested else "user",
                        current_task=task["id"],
                    )
                    return
                if state == "cancelled":
                    task["status"] = "cancelled"
                    task["outcome"] = outcome
                    self._save_plan(job.plan_path, plan)
                    self.registry.update(
                        job_id,
                        status="cancelled",
                        active_elapsed_seconds=job.active_elapsed_seconds
                        + (time.monotonic() - active_started),
                    )
                    return
                if process.returncode != 0 or not outcome.get("metrics"):
                    task["status"] = "failed"
                    task["error"] = (stderr.strip() or stdout.strip())[-4_000:]
                    root_error = self._root_error(task["error"])
                    self._save_plan(job.plan_path, plan)
                    self._append_event(
                        job.event_path,
                        {
                            "run_id": job_id,
                            "phase": "setup",
                            "status": "failed",
                            "message": root_error,
                            "metadata": {
                                "task_id": task["id"],
                                "attempt": attempt,
                            },
                        },
                    )
                    self.registry.update(
                        job_id,
                        status="failed",
                        error=f"{task['id']}: {root_error}",
                    )
                    return
                task["status"] = "completed"
                task["outcome"] = outcome
                completed += 1
                elapsed = max(time.monotonic() - started, 0.001)
                average = elapsed / max(completed, 1)
                remaining = max(len(tasks) - completed, 0)
                confidence = (
                    "yüksek"
                    if completed >= 5
                    else "orta" if completed >= 2 else "düşük"
                )
                self._save_plan(job.plan_path, plan)
                self.registry.update(
                    job_id,
                    progress=completed / len(tasks) * 98.0,
                    eta_seconds=int(average * remaining),
                    eta_confidence=confidence,
                )
            self.registry.update(job_id, progress=99.0, current_task=None)
            self._append_event(
                job.event_path,
                {
                    "run_id": job_id,
                    "phase": "verification",
                    "status": "started",
                    "message": "Sonuç kapsamı doğrulanıyor",
                },
            )
            verification = self._verify_shards(plan)
            aggregate = self._aggregate(plan)
            aggregate["bundles"] = verification["bundles"]
            aggregate["verified"] = bool(aggregate["verified"]) and bool(
                verification["verified"]
            )
            aggregate_path = Path(job.plan_path).parent / "aggregate.json"
            aggregate_path.write_text(
                json.dumps(aggregate, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            self.registry.update(
                job_id,
                status="completed",
                progress=100.0,
                eta_seconds=0,
                eta_confidence="yüksek",
                result=aggregate,
                provisional_result=aggregate,
                active_elapsed_seconds=job.active_elapsed_seconds
                + (time.monotonic() - active_started),
            )
            self._record_runtime(
                plan,
                job.active_elapsed_seconds + (time.monotonic() - active_started),
            )
            self._append_event(
                job.event_path,
                {
                    "run_id": job_id,
                    "phase": "verification",
                    "status": "completed",
                    "message": "Sonuçlar doğrulandı",
                    "metadata": {"verified": aggregate["verified"]},
                },
            )
        except Exception as exc:
            self.registry.update(
                job_id,
                status="failed",
                pid=None,
                error=str(exc)[-4_000:],
                active_elapsed_seconds=job.active_elapsed_seconds
                + (time.monotonic() - active_started),
            )
        finally:
            with self._lock:
                self._processes.pop(job_id, None)

    def _warmup_ollama(self, job_id: str, event_path: str, client_id: str) -> None:
        ollama = self.active_ollama()
        if not ollama.get("url") or not ollama.get("model"):
            return
        self._append_event(
            event_path,
            {
                "run_id": job_id,
                "phase": "setup",
                "status": "warmup_started",
                "message": f"{client_id} · skorlanmayan model warm-up",
            },
        )
        try:
            response = httpx.post(
                f"{str(ollama['url']).rstrip('/')}/api/generate",
                json={
                    "model": ollama["model"],
                    "prompt": "Yalnızca 'hazır' yaz.",
                    "stream": False,
                    "options": {"temperature": 0},
                },
                timeout=30.0,
            )
            response.raise_for_status()
            status = "warmup_completed"
            message = f"{client_id} · warm-up tamamlandı"
        except Exception as exc:
            status = "warmup_warning"
            message = f"{client_id} · warm-up başarısız: {str(exc)[:180]}"
        self._append_event(
            event_path,
            {
                "run_id": job_id,
                "phase": "setup",
                "status": status,
                "message": message,
            },
        )

    def _monitor_task(
        self,
        job_id: str,
        event_path: str,
        plan: dict[str, Any],
        task: dict[str, Any],
        completed_tasks: int,
        event_offset: int,
        elapsed_active: float,
    ) -> None:
        lines = Path(event_path).read_text(encoding="utf-8").splitlines()
        events: list[dict[str, Any]] = []
        for line in lines[event_offset:]:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        latest = events[-1] if events else {}
        question_index = int(latest.get("question_index") or 0)
        question_total = int(latest.get("question_total") or 0)
        scenario_index = int(latest.get("scenario_index") or 0)
        scenario_total = int(latest.get("scenario_total") or 0)
        phase = str(latest.get("phase") or "setup")
        status = str(latest.get("status") or "running")
        phase_weight = {
            "preflight": 0.01,
            "setup": 0.02,
            "purge": 0.04,
            "ingest": 0.10,
            "retrieval": 0.25,
            "generation": 0.55,
            "evaluation": 0.85,
            "reporting": 0.98,
            "verification": 0.99,
        }.get(phase, 0.02)
        if status == "completed":
            phase_weight = min(phase_weight + 0.12, 1.0)
        if question_total and question_index:
            completed_questions = max(question_index - 1, 0)
            task_ratio = min(
                (completed_questions + phase_weight) / question_total,
                0.99,
            )
        elif scenario_total:
            completed_scenarios = max(scenario_index - 1, 0)
            context_index = int(latest.get("context_index") or 0)
            context_total = int(latest.get("context_total") or 0)
            ingest_fraction = (
                context_index / context_total if context_total else phase_weight
            )
            scenario_fraction = (
                completed_scenarios + min(ingest_fraction, 1.0)
            ) / scenario_total
            task_ratio = min(max(phase_weight, 0.04) * scenario_fraction, 0.12)
        else:
            task_ratio = phase_weight
        task_total = max(len(plan["tasks"]), 1)
        progress = min(((completed_tasks + task_ratio) / task_total) * 98.0, 98.0)
        current = self.registry.get(job_id)
        if current:
            progress = max(progress, current.progress)
        eta = (
            int(elapsed_active / progress * (98.0 - progress))
            if progress > 0.5
            else None
        )
        confidence = (
            "yüksek"
            if question_index >= 20
            else "orta" if question_index >= 3 else "düşük"
        )
        metadata = latest.get("metadata") or {}
        results_file = next(
            (
                (event.get("metadata") or {}).get("results_file")
                for event in reversed(events)
                if (event.get("metadata") or {}).get("results_file")
            ),
            None,
        )
        provisional = self._provisional_from_results(
            plan, task, str(results_file) if results_file else None
        )
        snapshot = {
            "task_id": task["id"],
            "client": task["client"],
            "shard_id": task["shard_id"],
            "task_index": completed_tasks + 1,
            "task_total": task_total,
            "task_progress": task_ratio * 100.0,
            "phase": latest.get("phase", "setup"),
            "status": latest.get("status", "running"),
            "scenario_index": scenario_index,
            "scenario_total": scenario_total,
            "context_index": latest.get("context_index"),
            "context_total": latest.get("context_total"),
            "question_index": question_index,
            "question_total": question_total,
            "question_id": latest.get("question_id"),
            "elapsed_active_seconds": elapsed_active,
            "metadata": metadata,
        }
        self.registry.update(
            job_id,
            progress=progress,
            eta_seconds=eta,
            eta_confidence=confidence,
            active_elapsed_seconds=elapsed_active,
            progress_snapshot=snapshot,
            provisional_result=provisional,
        )

    @classmethod
    def _provisional_from_results(
        cls,
        plan: dict[str, Any],
        active_task: dict[str, Any],
        results_file: str | None,
    ) -> dict[str, Any] | None:
        rows_by_client: dict[str, list[dict[str, Any]]] = {}
        for task in plan["tasks"]:
            outcome = task.get("outcome") or {}
            path = outcome.get("results_file")
            if task is active_task and results_file:
                path = results_file
            if not path or not Path(path).is_file():
                continue
            rows = [
                json.loads(line)
                for line in Path(path).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            rows_by_client.setdefault(task["client"], []).extend(rows)
        if not rows_by_client:
            return None
        return cls._summary_from_rows(plan, rows_by_client, verified=False)

    @staticmethod
    def _aggregate(plan: dict[str, Any]) -> dict[str, Any]:
        systems: dict[str, list[dict[str, Any]]] = {}
        verified = True
        total_rows = 0
        for task in plan["tasks"]:
            outcome = task.get("outcome") or {}
            metrics = outcome.get("metrics") or {}
            if task.get("status") != "completed" or not metrics.get("valid", False):
                verified = False
                continue
            path = Path(outcome["results_file"])
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            expected = next(
                shard["questions"]
                for shard in plan["shards"]
                if shard["id"] == task["shard_id"]
            )
            if len(rows) != expected:
                verified = False
            total_rows += len(rows)
            systems.setdefault(task["client"], []).extend(rows)
        result = JobManager._summary_from_rows(plan, systems, verified=verified)
        result["total_rows"] = total_rows
        return result

    @staticmethod
    def _summary_from_rows(
        plan: dict[str, Any],
        systems: dict[str, list[dict[str, Any]]],
        *,
        verified: bool,
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for client, rows in systems.items():
            count = len(rows)
            retrieval_rows = [row for row in rows if row.get("supporting_context_ids")]

            def hit_at(k: int) -> float:
                if not retrieval_rows:
                    return 0.0
                return sum(
                    bool(
                        set(row.get("supporting_context_ids", [])).intersection(
                            row.get("retrieved_context_ids", [])[:k]
                        )
                    )
                    for row in retrieval_rows
                ) / len(retrieval_rows)

            reciprocal_ranks: list[float] = []
            ndcg_values: list[float] = []
            for row in retrieval_rows:
                expected = set(row.get("supporting_context_ids", []))
                retrieved = row.get("retrieved_context_ids", [])
                rank = next(
                    (
                        index
                        for index, context_id in enumerate(retrieved, start=1)
                        if context_id in expected
                    ),
                    None,
                )
                reciprocal_ranks.append(1.0 / rank if rank else 0.0)
                dcg = sum(
                    1.0 / math.log2(index + 1)
                    for index, context_id in enumerate(retrieved[:5], start=1)
                    if context_id in expected
                )
                ideal_count = min(len(expected), 5)
                ideal = sum(
                    1.0 / math.log2(index + 1) for index in range(1, ideal_count + 1)
                )
                ndcg_values.append(dcg / ideal if ideal else 0.0)
            answer_f1 = [
                float(row["answer_token_f1"])
                for row in rows
                if row.get("answer_token_f1") is not None
            ]
            exact = [
                float(row["answer_exact_match"])
                for row in rows
                if row.get("answer_exact_match") is not None
            ]
            retrieval_latency = [
                float(row["retrieval_latency_ms"])
                for row in rows
                if row.get("retrieval_latency_ms") is not None
            ]
            generation_latency = [
                float(row["generation_latency_ms"])
                for row in rows
                if row.get("generation_latency_ms") is not None
            ]
            errors = sum(bool(row.get("infrastructure_error")) for row in rows)
            summary[client] = {
                "questions": count,
                "valid": errors == 0,
                "infrastructure_errors": errors,
                "accuracy": (
                    sum(bool(row.get("is_correct")) for row in rows) / count
                    if count
                    else 0.0
                ),
                "hit_at_1": hit_at(1),
                "hit_at_3": hit_at(3),
                "hit_at_5": hit_at(5),
                "mrr": (
                    sum(reciprocal_ranks) / len(reciprocal_ranks)
                    if reciprocal_ranks
                    else 0.0
                ),
                "ndcg": (sum(ndcg_values) / len(ndcg_values) if ndcg_values else 0.0),
                "exact_match": sum(exact) / len(exact) if exact else None,
                "token_f1": sum(answer_f1) / len(answer_f1) if answer_f1 else None,
                "avg_retrieval_ms": (
                    sum(retrieval_latency) / len(retrieval_latency)
                    if retrieval_latency
                    else None
                ),
                "avg_generation_ms": (
                    sum(generation_latency) / len(generation_latency)
                    if generation_latency
                    else None
                ),
            }
        return {
            "schema_version": 1,
            "job_id": plan["id"],
            "profile": plan["profile"],
            "verified": verified and bool(systems),
            "provisional": not verified,
            "total_rows": sum(len(rows) for rows in systems.values()),
            "systems": summary,
        }

    @staticmethod
    def _verify_shards(plan: dict[str, Any]) -> dict[str, Any]:
        from ..core.preflight import file_sha256
        from ..core.suite import verify_results

        plan_root = Path(plan["tasks"][0]["config"]).parents[1]
        bundle_root = plan_root / "bundles"
        bundle_root.mkdir(exist_ok=True)
        verified = True
        bundles: list[dict[str, Any]] = []
        for shard in plan["shards"]:
            shard_tasks = [
                task for task in plan["tasks"] if task["shard_id"] == shard["id"]
            ]
            runs: list[dict[str, Any]] = []
            for task in shard_tasks:
                outcome = task.get("outcome") or {}
                if task.get("status") != "completed" or not outcome.get("metrics"):
                    verified = False
                    continue
                results = Path(outcome["results_file"]).resolve()
                manifest = results.parent / f"manifest_{outcome['run_id']}.json"
                if not manifest.exists():
                    verified = False
                    continue
                runs.append(
                    {
                        "id": task["id"],
                        "group": shard["id"],
                        "system": task["client"],
                        "config": str(Path(task["config"]).resolve()),
                        "config_file_sha256": file_sha256(task["config"]),
                        "results": str(results),
                        "manifest": str(manifest.resolve()),
                        "metrics": outcome["metrics"],
                    }
                )
            bundle_dir = bundle_root / shard["id"]
            bundle_dir.mkdir(exist_ok=True)
            bundle = {
                "schema_version": 3,
                "bundle_id": str(uuid.uuid4()),
                "suite": plan["name"],
                "suite_sha256": plan["plan_sha256"],
                "profile": "internal",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "code_sha256": plan["plan_sha256"],
                "required_systems": plan["clients"],
                "judge_calibration_path": None,
                "runs": runs,
            }
            bundle_path = bundle_dir / "bundle.json"
            bundle_path.write_text(
                json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            try:
                result = verify_results(bundle_path)
            except Exception as exc:
                result = {"protocol_valid": False, "errors": [str(exc)]}
            (bundle_dir / "verification.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if not result.get("protocol_valid", False):
                verified = False
            bundles.append(
                {
                    "shard_id": shard["id"],
                    "bundle": str(bundle_path),
                    "verification": result,
                }
            )
        return {"verified": verified and bool(bundles), "bundles": bundles}

    def control(self, job_id: str, action: str) -> dict[str, Any]:
        job = self.registry.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if action not in {"pause", "cancel"}:
            raise ValueError(action)
        if job.status not in {"running", "queued"}:
            raise ValueError(f"{job.status} durumundaki iş kontrol edilemez")
        if job.status == "queued":
            if action != "cancel":
                raise ValueError("Kuyruktaki iş yalnız durdurulabilir")
            plan = self._load_plan(job.plan_path)
            for task in plan["tasks"]:
                if task["status"] == "queued":
                    task["status"] = "cancelled"
            self._save_plan(job.plan_path, plan)
            self.registry.update(
                job_id, status="cancelled", queue_position=None, pause_reason=None
            )
            with self._condition:
                self._refresh_queue_positions()
            return {"accepted": True, "action": action}
        control_path = Path(job.plan_path).parent / "control.json"
        control_path.write_text(
            json.dumps({"action": action, "requested_at": time.time()}) + "\n",
            encoding="utf-8",
        )
        self._append_event(
            job.event_path,
            {
                "run_id": job_id,
                "phase": "control",
                "status": "requested",
                "message": action,
            },
        )
        return {"accepted": True, "action": action}

    def resume(self, job_id: str) -> dict[str, Any]:
        job = self.registry.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status not in {"paused", "failed"}:
            raise ValueError("yalnız paused veya failed işler devam ettirilebilir")
        if (
            job.time_limit_minutes is not None
            and job.active_elapsed_seconds >= job.time_limit_minutes * 60.0
        ):
            raise ValueError("Devam etmeden önce süre ekleyin veya limiti kaldırın")
        plan = self._load_plan(job.plan_path)
        for task in plan["tasks"]:
            if task["status"] in {"paused", "failed"}:
                task["status"] = "queued"
                task.pop("error", None)
                break
        self._save_plan(job.plan_path, plan)
        self.registry.update(
            job_id,
            status="queued",
            error=None,
            pause_reason=None,
            progress_snapshot=None,
        )
        with self._condition:
            self._refresh_queue_positions()
            self._condition.notify_all()
        return {"accepted": True}

    def retry_failed(self, job_id: str) -> dict[str, Any]:
        job = self.registry.get(job_id)
        if job is None:
            raise KeyError(job_id)
        plan = self._load_plan(job.plan_path)
        changed = 0
        for task in plan["tasks"]:
            if task["status"] == "failed":
                task["status"] = "queued"
                task["attempt"] = int(task.get("attempt", 1)) + 1
                task.pop("error", None)
                changed += 1
        if not changed:
            raise ValueError("tekrar çalıştırılacak başarısız task yok")
        self._save_plan(job.plan_path, plan)
        self.registry.update(job_id, status="queued", error=None, pause_reason=None)
        with self._condition:
            self._refresh_queue_positions()
            self._condition.notify_all()
        return {"accepted": True, "tasks": changed}

    def extend_time(
        self, job_id: str, *, minutes: float | None, remove_limit: bool
    ) -> dict[str, Any]:
        job = self.registry.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if remove_limit:
            updated = self.registry.update(job_id, time_limit_minutes=None)
        elif minutes is not None:
            current = job.time_limit_minutes or (job.active_elapsed_seconds / 60.0)
            updated = self.registry.update(job_id, time_limit_minutes=current + minutes)
        else:
            raise ValueError("minutes veya remove_limit gereklidir")
        plan = self._load_plan(job.plan_path)
        plan["time_limit_minutes"] = updated.time_limit_minutes
        plan["request"]["time_limit_minutes"] = updated.time_limit_minutes
        self._save_plan(job.plan_path, plan)
        return {
            "accepted": True,
            "time_limit_minutes": updated.time_limit_minutes,
            "active_elapsed_seconds": updated.active_elapsed_seconds,
        }

    def sync_dataset(self, dataset_id: str) -> dict[str, Any]:
        spec = dataset_spec(dataset_id)
        target = spec.get("sync_target")
        if not target:
            raise ValueError("Bu dataset paketle birlikte gelir; sync gerekmez")
        if any(
            item.status in {"running", "queued"}
            for item in self.registry.list(include_archived=True)
        ):
            raise ValueError("Dataset sync için aktif benchmark kuyruğu boş olmalıdır")
        with self._lock:
            if self._dataset_thread and self._dataset_thread.is_alive():
                raise ValueError("Başka bir dataset sync işlemi çalışıyor")
            operation_id = str(uuid.uuid4())
            operation = self.registry.create_dataset_operation(operation_id, dataset_id)
            self._dataset_thread = threading.Thread(
                target=self._run_dataset_sync,
                args=(operation_id, dataset_id, str(target)),
                name=f"dataset-sync-{dataset_id}",
                daemon=True,
            )
            self._dataset_thread.start()
        return operation.model_dump()

    def _run_dataset_sync(
        self, operation_id: str, dataset_id: str, target: str
    ) -> None:
        self.registry.update_dataset_operation(
            operation_id, status="running", progress=5.0
        )
        try:
            result = sync_target(target)
            detail = dataset_detail(dataset_id)
            self.registry.update_dataset_operation(
                operation_id,
                status="completed",
                progress=100.0,
                result={"sync": result, "dataset": detail},
            )
        except Exception as exc:
            self.registry.update_dataset_operation(
                operation_id,
                status="failed",
                progress=100.0,
                error=str(exc)[-4_000:],
            )

    @staticmethod
    def _sanitize_log(value: str) -> str:
        sanitized = value
        sanitized = _SECRET_PATTERNS[0].sub(r"\1\2***", sanitized)
        sanitized = _SECRET_PATTERNS[1].sub(r"\1***:***@", sanitized)
        return sanitized

    @classmethod
    def _root_error(cls, value: str) -> str:
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if not lines:
            return "Task çıktı üretmeden başarısız oldu"
        preferred = next(
            (
                line
                for line in reversed(lines)
                if line.startswith(
                    (
                        "ValueError:",
                        "RuntimeError:",
                        "ImportError:",
                        "TimeoutError:",
                        "ConnectionError:",
                    )
                )
            ),
            lines[-1],
        )
        return cls._sanitize_log(preferred)[:1_000]

    @staticmethod
    def _resolution_for(error: str) -> str:
        lowered = error.casefold()
        if "requires unavailable evaluators" in lowered:
            return (
                "Bu dataset rubric/LLM judge gerektiriyor. Ollama'yı bağlayın, "
                "yeni benchmarkta bağımsız judge'ı etkinleştirip farklı bir judge "
                "modeli seçin."
            )
        if "ollama" in lowered or "generator model" in lowered:
            return (
                "Üst çubuktaki Ollama rozetinden bağlantıyı test edin ve gerekli "
                "generator/judge modelini seçin."
            )
        if "checksum" in lowered or "dataset" in lowered and "not found" in lowered:
            return "Datasetler ekranından checksum/hazır durumunu kontrol edin ve gerekirse sync çalıştırın."
        if "importerror" in lowered or "not installed" in lowered:
            return "Client ekranındaki dependency/capability durumunu kontrol edin."
        return (
            "Aşağıdaki stderr ve son olayları inceleyin; ayarı düzelttikten sonra "
            "aynı taskı tekrar deneyin."
        )

    def diagnostics(self, job_id: str) -> dict[str, Any]:
        job = self.registry.get(job_id)
        if job is None:
            raise KeyError(job_id)
        plan = self._load_plan(job.plan_path)
        failed_tasks: list[dict[str, Any]] = []
        for task in plan.get("tasks", []):
            if task.get("status") != "failed":
                continue
            error = self._sanitize_log(str(task.get("error") or ""))
            logs: dict[str, str] = {}
            for kind in ("stdout", "stderr"):
                path = task.get(f"{kind}_log")
                if path and Path(path).is_file():
                    logs[kind] = self._sanitize_log(
                        Path(path).read_text(encoding="utf-8")[-20_000:]
                    )
            failed_tasks.append(
                {
                    "id": task["id"],
                    "client": task["client"],
                    "shard_id": task["shard_id"],
                    "attempt": task.get("attempt", 1),
                    "root_error": self._root_error(error),
                    "resolution": self._resolution_for(error),
                    "traceback": error,
                    "logs": logs,
                }
            )

        recent_events: list[dict[str, Any]] = []
        event_path = Path(job.event_path)
        if event_path.is_file():
            for line in event_path.read_text(encoding="utf-8").splitlines()[-100:]:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event["message"] = self._sanitize_log(str(event.get("message") or ""))
                recent_events.append(event)

        checks: list[dict[str, str]] = []
        result = job.result or job.provisional_result
        if result:
            checks.append(
                {
                    "status": "passed" if result.get("verified") else "warning",
                    "label": "Bundle verification",
                    "detail": (
                        "Protokol doğrulandı"
                        if result.get("verified")
                        else "Sonuç henüz doğrulanmadı veya kısmi"
                    ),
                }
            )
            for client, metrics in result.get("systems", {}).items():
                valid = bool(metrics.get("valid"))
                checks.append(
                    {
                        "status": "passed" if valid else "failed",
                        "label": f"{client} sonuç geçerliliği",
                        "detail": (
                            f"{metrics.get('questions', 0)} soru · "
                            f"{metrics.get('infrastructure_errors', 0)} altyapı hatası"
                        ),
                    }
                )
                latencies = (
                    metrics.get("avg_retrieval_ms"),
                    metrics.get("avg_generation_ms"),
                )
                if any(value is not None and float(value) < 0 for value in latencies):
                    checks.append(
                        {
                            "status": "failed",
                            "label": f"{client} latency alanları",
                            "detail": "Negatif latency tespit edildi",
                        }
                    )
                if (
                    int(metrics.get("questions") or 0) <= 10
                    and float(metrics.get("hit_at_1") or 0) == 1.0
                ):
                    checks.append(
                        {
                            "status": "info",
                            "label": f"{client} küçük örneklem",
                            "detail": "Kusursuz skor yalnız smoke göstergesidir; kalite üstünlüğü değildir",
                        }
                    )
        else:
            checks.append(
                {
                    "status": "failed" if job.status == "failed" else "info",
                    "label": "Sonuç üretimi",
                    "detail": "Henüz aggregate sonuç oluşmadı",
                }
            )

        root = Path(job.plan_path).parent
        try:
            artifact_root = str(root.relative_to(self.results_root))
        except ValueError:
            artifact_root = root.name
        return {
            "job_id": job.id,
            "status": job.status,
            "summary": failed_tasks[0]["root_error"] if failed_tasks else job.error,
            "failed_tasks": failed_tasks,
            "checks": checks,
            "recent_events": recent_events,
            "artifacts": {
                "root": artifact_root,
                "plan": f"{artifact_root}/plan.json",
                "events": f"{artifact_root}/events.jsonl",
                "logs": f"{artifact_root}/logs/",
            },
        }
