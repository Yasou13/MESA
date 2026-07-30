from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import psutil
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..core.paths import resolve_results_root
from .catalog import (
    client_catalog,
    dataset_catalog,
    dataset_detail,
    dataset_scenarios,
    dataset_spec,
)
from .exporter import build_export
from .jobs import JobManager
from .models import (
    ControlRequest,
    DatasetSyncRequest,
    OllamaSettingsRequest,
    OllamaTestRequest,
    PlanRequest,
    TimeExtensionRequest,
)
from .ollama import inspect_ollama, validate_ollama_url
from .planner import preview_plan
from .registry import JobRegistry


def _safe_job(manager: JobManager, job_id: str) -> Any:
    job = manager.registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Benchmark işi bulunamadı")
    return job


async def _system_snapshot(ollama: dict[str, Any]) -> dict[str, Any]:
    disk = psutil.disk_usage(str(Path.cwd()))
    snapshot: dict[str, Any] = {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_percent": disk.percent,
        "ollama": {"online": False, "model": None, "latency_ms": None},
        "gpu": None,
    }
    base_url = str(ollama.get("url") or "").rstrip("/")
    if base_url:
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{base_url}/api/ps")
                response.raise_for_status()
                models = response.json().get("models", [])
            snapshot["ollama"] = {
                "online": True,
                "model": ollama.get("model")
                or (models[0].get("name") if models else None),
                "url": base_url,
                "models": [item.get("name") or item.get("model") for item in models],
                "latency_ms": (time.perf_counter() - started) * 1000.0,
            }
        except Exception:
            pass
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            result = subprocess.run(
                [
                    nvidia_smi,
                    "--query-gpu=utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
            utilization, used, total = [
                int(item.strip()) for item in result.stdout.splitlines()[0].split(",")
            ]
            snapshot["gpu"] = {
                "utilization_percent": utilization,
                "memory_used_mb": used,
                "memory_total_mb": total,
            }
        except Exception:
            pass
    return snapshot


def create_dashboard_app(
    *, results_root: str | Path | None = None, static_root: str | Path | None = None
) -> FastAPI:
    root = resolve_results_root(results_root)
    dashboard_root = root / "dashboard"
    registry = JobRegistry(dashboard_root / "dashboard.sqlite3")
    manager = JobManager(registry, root)
    app = FastAPI(title="MESA Benchmark Console", version="1.0.0")
    app.state.job_manager = manager

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "local_only": True}

    @app.get("/api/catalog")
    async def catalog() -> dict[str, Any]:
        return {
            "datasets": dataset_catalog(),
            "clients": client_catalog(),
            "profiles": [
                {
                    "id": "quality",
                    "name": "Quality",
                    "description": "Eşdeğer doğrudan ingest ile retrieval ve QA kalitesi",
                    "available": True,
                },
                {
                    "id": "native",
                    "name": "Native Memory",
                    "description": "Native extraction ve time-to-searchable",
                    "available": True,
                },
                {
                    "id": "capacity",
                    "name": "Capacity",
                    "description": "Generation kapalı büyük ölçek ingest ve retrieval",
                    "available": True,
                },
            ],
        }

    @app.get("/api/system")
    async def system() -> dict[str, Any]:
        return await _system_snapshot(manager.active_ollama())

    @app.get("/api/settings/ollama")
    async def get_ollama_settings() -> dict[str, Any]:
        active = manager.active_ollama()
        value: dict[str, Any] = {
            "url": active.get("url"),
            "model": active.get("model"),
            "source": "saved" if registry.get_setting("ollama") else "environment",
            "online": False,
            "models": [],
            "error": None,
        }
        if active.get("url"):
            try:
                checked = await inspect_ollama(str(active["url"]))
                value.update(checked)
            except Exception as exc:
                value["error"] = str(exc)
        return value

    @app.post("/api/settings/ollama/test")
    async def test_ollama(request: OllamaTestRequest) -> dict[str, Any]:
        try:
            return await inspect_ollama(request.url)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/settings/ollama")
    async def save_ollama(request: OllamaSettingsRequest) -> dict[str, Any]:
        try:
            url = validate_ollama_url(request.url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        registry.set_setting("ollama", {"url": url, "model": request.model})
        return {"saved": True, "url": url, "model": request.model}

    @app.delete("/api/settings/ollama")
    async def delete_ollama() -> dict[str, Any]:
        registry.delete_setting("ollama")
        return {"saved": False, "active": manager.active_ollama()}

    @app.get("/api/datasets")
    async def datasets() -> list[dict[str, Any]]:
        return dataset_catalog()

    @app.get("/api/datasets/{dataset_id}")
    async def get_dataset(dataset_id: str) -> dict[str, Any]:
        try:
            return dataset_detail(dataset_id)
        except (KeyError, StopIteration):
            raise HTTPException(status_code=404, detail="Dataset bulunamadı")

    @app.get("/api/datasets/{dataset_id}/scenarios")
    async def get_dataset_scenarios(
        dataset_id: str, offset: int = 0, limit: int = 10
    ) -> dict[str, Any]:
        if offset < 0 or limit < 1 or limit > 50:
            raise HTTPException(status_code=422, detail="Geçersiz sayfalama")
        try:
            return dataset_scenarios(dataset_id, offset=offset, limit=limit)
        except KeyError:
            raise HTTPException(status_code=404, detail="Dataset bulunamadı")
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/datasets/{dataset_id}/sync", status_code=202)
    async def sync_dataset(
        dataset_id: str, request: DatasetSyncRequest
    ) -> dict[str, Any]:
        if not request.confirm:
            raise HTTPException(status_code=422, detail="Açık indirme onayı gereklidir")
        try:
            dataset_spec(dataset_id)
            return manager.sync_dataset(dataset_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Dataset bulunamadı")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/dataset-operations/{operation_id}")
    async def dataset_operation(operation_id: str) -> dict[str, Any]:
        operation = registry.get_dataset_operation(operation_id)
        if operation is None:
            raise HTTPException(status_code=404, detail="Dataset işlemi bulunamadı")
        return operation.model_dump()

    @app.post("/api/plans/preview")
    async def plan_preview(request: PlanRequest) -> dict[str, Any]:
        try:
            ollama = manager.active_ollama()
            seconds_per_question, history_samples = manager.runtime_estimate(request)
            preview = preview_plan(
                request,
                ollama_configured=bool(ollama.get("url")),
                default_model=ollama.get("model"),
                seconds_per_question=seconds_per_question,
                history_samples=history_samples,
            )
            if preview["requires_ollama"] and ollama.get("url"):
                try:
                    checked = await inspect_ollama(str(ollama["url"]))
                    selected_model = preview.get("generator_model")
                    if selected_model and selected_model not in checked["models"]:
                        preview["blockers"].append(
                            f"Seçili model Ollama'da bulunamadı: {selected_model}"
                        )
                except Exception as exc:
                    preview["blockers"].append(f"Ollama preflight başarısız: {exc}")
                preview["ready"] = not preview["blockers"]
            return preview
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/jobs", status_code=202)
    async def create_job(request: PlanRequest) -> dict[str, Any]:
        try:
            ollama = manager.active_ollama()
            seconds_per_question, history_samples = manager.runtime_estimate(request)
            preview = preview_plan(
                request,
                ollama_configured=bool(ollama.get("url")),
                default_model=ollama.get("model"),
                seconds_per_question=seconds_per_question,
                history_samples=history_samples,
            )
            if preview["requires_ollama"] and ollama.get("url"):
                checked = await inspect_ollama(str(ollama["url"]))
                selected_model = preview.get("generator_model")
                if selected_model and selected_model not in checked["models"]:
                    raise ValueError(
                        f"Seçili model Ollama'da bulunamadı: {selected_model}"
                    )
            if not preview["ready"]:
                raise ValueError("; ".join(preview["blockers"]))
            return manager.create(request)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/jobs")
    async def list_jobs(include_archived: bool = False) -> list[dict[str, Any]]:
        return [
            item.model_dump()
            for item in registry.list(include_archived=include_archived)
        ]

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        job = _safe_job(manager, job_id)
        plan = json.loads(Path(job.plan_path).read_text(encoding="utf-8"))
        return {**job.model_dump(), "plan": plan}

    @app.get("/api/jobs/{job_id}/progress")
    async def job_progress(job_id: str) -> dict[str, Any]:
        job = _safe_job(manager, job_id)
        remaining = (
            max(
                job.time_limit_minutes * 60.0 - job.active_elapsed_seconds,
                0.0,
            )
            if job.time_limit_minutes is not None
            else None
        )
        return {
            "job_id": job.id,
            "status": job.status,
            "progress": job.progress,
            "eta_seconds": job.eta_seconds,
            "eta_confidence": job.eta_confidence,
            "active_elapsed_seconds": job.active_elapsed_seconds,
            "time_budget_remaining_seconds": remaining,
            "pause_reason": job.pause_reason,
            "snapshot": job.progress_snapshot,
            "provisional_result": job.provisional_result,
        }

    @app.get("/api/jobs/{job_id}/diagnostics")
    async def job_diagnostics(job_id: str) -> dict[str, Any]:
        _safe_job(manager, job_id)
        return manager.diagnostics(job_id)

    @app.post("/api/jobs/{job_id}/control", status_code=202)
    async def control_job(job_id: str, request: ControlRequest) -> dict[str, Any]:
        _safe_job(manager, job_id)
        try:
            return manager.control(job_id, request.action)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs/{job_id}/resume", status_code=202)
    async def resume_job(job_id: str) -> dict[str, Any]:
        _safe_job(manager, job_id)
        try:
            return manager.resume(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    async def retry_job(job_id: str) -> dict[str, Any]:
        _safe_job(manager, job_id)
        try:
            return manager.retry_failed(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs/{job_id}/extend-time", status_code=202)
    async def extend_time(job_id: str, request: TimeExtensionRequest) -> dict[str, Any]:
        _safe_job(manager, job_id)
        try:
            return manager.extend_time(
                job_id,
                minutes=request.minutes,
                remove_limit=request.remove_limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs/{job_id}/archive")
    async def archive_job(job_id: str) -> dict[str, Any]:
        job = _safe_job(manager, job_id)
        if job.status == "running":
            raise HTTPException(
                status_code=409, detail="Çalışan benchmark arşivlenemez"
            )
        return registry.update(job_id, archived=True).model_dump()

    @app.get("/api/results")
    async def results() -> list[dict[str, Any]]:
        return [
            item.model_dump()
            for item in registry.list(include_archived=False)
            if item.result is not None
        ]

    @app.get("/api/jobs/{job_id}/questions")
    async def questions(
        job_id: str, client: str | None = None, offset: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        job = _safe_job(manager, job_id)
        if offset < 0 or limit < 1 or limit > 500:
            raise HTTPException(status_code=422, detail="Geçersiz sayfalama")
        plan = json.loads(Path(job.plan_path).read_text(encoding="utf-8"))
        rows: list[dict[str, Any]] = []
        for task in plan["tasks"]:
            if client and task["client"] != client:
                continue
            outcome = task.get("outcome") or {}
            results_file = outcome.get("results_file")
            if not results_file or not Path(results_file).exists():
                continue
            for line in Path(results_file).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                rows.append(
                    {
                        "client": task["client"],
                        "shard_id": task["shard_id"],
                        "scenario_id": row.get("scenario_id"),
                        "question_id": row.get("question_id"),
                        "query": row.get("query"),
                        "ground_truth": row.get("ground_truth"),
                        "reference_answers": row.get("reference_answers", []),
                        "actual_answer": row.get("actual_answer"),
                        "expected_context_ids": row.get("expected_context_ids", []),
                        "retrieved_context_ids": row.get("retrieved_context_ids", []),
                        "score": row.get("score"),
                        "is_correct": row.get("is_correct"),
                        "failure_attribution": row.get("failure_attribution"),
                    }
                )
        return {
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "items": rows[offset : offset + limit],
        }

    @app.get("/api/jobs/{job_id}/export")
    async def export_job(job_id: str, format: str = "md") -> FileResponse:
        job = _safe_job(manager, job_id)
        plan = json.loads(Path(job.plan_path).read_text(encoding="utf-8"))
        try:
            target = build_export(job, plan, format)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        media_types = {
            "md": "text/markdown; charset=utf-8",
            "json": "application/json",
            "csv": "text/csv; charset=utf-8",
        }
        return FileResponse(
            target,
            media_type=media_types[format],
            filename=target.name,
        )

    @app.get("/api/jobs/{job_id}/events")
    async def events(
        job_id: str,
        after: int = 0,
        last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        job = _safe_job(manager, job_id)

        async def stream() -> AsyncIterator[str]:
            header_position = (
                int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
            )
            position = max(after, header_position, 0)
            idle_ticks = 0
            while True:
                lines = Path(job.event_path).read_text(encoding="utf-8").splitlines()
                while position < len(lines):
                    position += 1
                    yield f"id: {position}\ndata: {lines[position - 1]}\n\n"
                    idle_ticks = 0
                latest = registry.get(job_id)
                if latest and latest.status in {"completed", "failed", "cancelled"}:
                    idle_ticks += 1
                    if idle_ticks >= 2:
                        return
                yield ": keep-alive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    package_static = Path(__file__).parent / "static"
    selected_static = Path(static_root) if static_root else package_static
    if selected_static.exists() and (selected_static / "index.html").exists():
        assets = selected_static / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="dashboard-assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def frontend(path: str) -> FileResponse:
            candidate = (selected_static / path).resolve()
            if (
                path
                and candidate.is_relative_to(selected_static.resolve())
                and candidate.is_file()
            ):
                return FileResponse(candidate)
            return FileResponse(selected_static / "index.html")

    return app
