from __future__ import annotations

import json
from pathlib import Path

import httpx
import mesa_benchmark.dashboard.planner as planner_module
import pytest
import yaml
from mesa_benchmark.core.paths import resolve_benchmark_path
from mesa_benchmark.core.progress import BenchmarkControlRequested, ProgressSink
from mesa_benchmark.core.runner import BenchmarkRunner
from mesa_benchmark.dashboard.app import create_dashboard_app
from mesa_benchmark.dashboard.exporter import build_export
from mesa_benchmark.dashboard.jobs import JobManager
from mesa_benchmark.dashboard.models import DashboardJob, PlanRequest
from mesa_benchmark.dashboard.ollama import validate_ollama_url
from mesa_benchmark.dashboard.planner import materialize_plan, preview_plan
from mesa_benchmark.dashboard.registry import JobRegistry


@pytest.fixture
def available_test_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = [
        {**item, "available": True, "reason": None}
        for item in planner_module.client_catalog()
    ]
    monkeypatch.setattr(planner_module, "client_catalog", lambda: catalog)


def _request(**updates: object) -> PlanRequest:
    value: dict[str, object] = {
        "name": "Dashboard test",
        "profile": "quality",
        "config": "resource://configs/internal/smoke_dense.yaml",
        "clients": ["dense-rag"],
        "seed": 42,
        "shard_question_limit": 1,
        "shard_context_limit": 1,
    }
    value.update(updates)
    return PlanRequest.model_validate(value)


def test_dashboard_shards_are_deterministic_and_complete() -> None:
    first = preview_plan(_request())
    second = preview_plan(_request())
    assert first["shards"] == second["shards"]
    scenario_ids = [
        scenario_id
        for shard in first["shards"]
        for scenario_id in shard["scenario_ids"]
    ]
    assert len(scenario_ids) == len(set(scenario_ids))
    assert len(scenario_ids) == first["dataset"]["scenarios"]
    assert (
        sum(item["questions"] for item in first["shards"])
        == first["dataset"]["questions"]
    )


def test_dashboard_materializes_valid_shard_evidence(
    tmp_path: Path, available_test_clients: None
) -> None:
    plan = materialize_plan(_request(), results_root=tmp_path)
    assert len(plan["shards"]) == 2
    assert len(plan["tasks"]) == 2
    for shard in plan["shards"]:
        dataset = Path(shard["dataset"])
        manifest = json.loads(Path(shard["manifest"]).read_text(encoding="utf-8"))
        assert dataset.exists()
        assert manifest["counts"]["questions"] == shard["questions"]
        assert manifest["converter"]["parameters"]["source_converted_sha256"]
    assert Path(plan["tasks"][0]["config"]).exists()


def test_all_shard_modes_and_manual_client_order_are_preserved(
    tmp_path: Path, available_test_clients: None
) -> None:
    fixed = preview_plan(_request(shard_mode="fixed_count", shard_count=2))
    limited = preview_plan(
        _request(
            shard_mode="limits",
            shard_question_limit=1,
            shard_context_limit=100,
        )
    )
    automatic = preview_plan(
        _request(shard_mode="auto_duration", target_shard_minutes=1),
        seconds_per_question=40.0,
        history_samples=3,
    )
    assert len(fixed["shards"]) == 2
    assert len(limited["shards"]) == 2
    assert len(automatic["shards"]) == 2
    assert automatic["estimated_total_seconds"] == 80
    assert automatic["eta_confidence"] == "orta"

    plan = materialize_plan(
        _request(
            clients=["mem0", "mesa", "dense-rag"],
            shard_mode="fixed_count",
            shard_count=1,
        ),
        results_root=tmp_path,
    )
    assert [task["client"] for task in plan["tasks"]] == [
        "mem0",
        "mesa",
        "dense-rag",
    ]


def test_semantic_dataset_is_blocked_before_run_without_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_config_path = planner_module.resolve_config_path(
        "resource://configs/internal/smoke_dense.yaml"
    )
    config = yaml.safe_load(source_config_path.read_text(encoding="utf-8"))
    source_dataset = resolve_benchmark_path(
        config["dataset"]["path"], base_dir=source_config_path.parent
    )
    scenarios = json.loads(source_dataset.read_text(encoding="utf-8"))
    scenarios[0]["questions"][0]["evaluation_strategy"] = "rubric_judge"
    dataset_path = tmp_path / "semantic.json"
    dataset_path.write_text(json.dumps(scenarios), encoding="utf-8")
    config["dataset"]["path"] = str(dataset_path)
    config_path = tmp_path / "semantic.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setattr(
        planner_module, "resolve_config_path", lambda _value: config_path
    )
    request = _request(
        config="test://semantic",
        generation_enabled=False,
        judge_enabled=False,
        shard_mode="fixed_count",
        shard_count=1,
    )
    preview = preview_plan(request)
    assert preview["ready"] is False
    assert "rubric_judge" in preview["required_evaluators"]
    assert any(
        "Bağımsız judge'ı etkinleştirin" in blocker for blocker in preview["blockers"]
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:11434/", "http://127.0.0.1:11434"),
        ("http://192.168.1.103:11434", "http://192.168.1.103:11434"),
        ("http://[::1]:11434", "http://[::1]:11434"),
    ],
)
def test_ollama_url_accepts_only_clean_local_roots(value: str, expected: str) -> None:
    assert validate_ollama_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com",
        "http://" + "user" + ":pass@" + "127.0.0.1:11434",
        "http://127.0.0.1:11434/api/tags",
        "http://127.0.0.1:11434?token=secret",
        "file:///tmp/ollama.sock",
    ],
)
def test_ollama_url_rejects_public_credentials_and_extra_parts(value: str) -> None:
    with pytest.raises(ValueError):
        validate_ollama_url(value)


def test_exports_are_partial_sanitized_and_csv_safe(tmp_path: Path) -> None:
    results = tmp_path / "raw.jsonl"
    results.write_text(
        json.dumps(
            {
                "scenario_id": "s1",
                "question_id": "q1",
                "query": '=HYPERLINK("https://bad")',
                "actual_answer": "+cmd",
                "is_correct": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    plan = {
        "schema_version": 1,
        "id": "job-1",
        "name": "Export test",
        "profile": "quality",
        "created_at": "2026-01-01T00:00:00+00:00",
        "seed": 42,
        "clients": ["dense-rag"],
        "source_dataset_sha256": "abc",
        "shard_mode": "limits",
        "target_shard_minutes": 20,
        "time_limit_minutes": None,
        "request": {
            "config": "/absolute/private/config.yaml",
            "clients": ["dense-rag"],
        },
        "shards": [],
        "tasks": [
            {
                "id": "t1",
                "shard_id": "s1",
                "client": "dense-rag",
                "status": "paused",
                "attempt": 1,
                "outcome": {"results_file": str(results)},
            }
        ],
    }
    job = DashboardJob(
        id="job-1",
        name="Export test",
        profile="quality",
        status="paused",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        plan_path=str(tmp_path / "plan.json"),
        event_path=str(tmp_path / "events.jsonl"),
        provisional_result={
            "verified": False,
            "systems": {},
            "bundles": [{"bundle": "/absolute/private/bundle.json"}],
        },
    )
    markdown = build_export(job, plan, "md").read_text(encoding="utf-8")
    json_export = build_export(job, plan, "json").read_text(encoding="utf-8")
    csv_export = build_export(job, plan, "csv").read_text(encoding="utf-8")
    assert "Geçici / Kısmi" in markdown
    assert "/absolute/private" not in json_export
    assert "'=HYPERLINK" in csv_export
    assert "'+cmd" in csv_export


def test_restart_reconciles_interrupted_dataset_operation(tmp_path: Path) -> None:
    registry = JobRegistry(tmp_path / "dashboard.sqlite3")
    operation = registry.create_dataset_operation("sync-1", "beam")
    registry.update_dataset_operation(operation.id, status="running", progress=30)
    JobManager(registry, tmp_path)
    reconciled = registry.get_dataset_operation(operation.id)
    assert reconciled is not None
    assert reconciled.status == "failed"
    assert reconciled.progress == 100
    assert "yeniden başlat" in (reconciled.error or "")


def test_job_diagnostics_exposes_root_cause_without_secrets(
    tmp_path: Path, available_test_clients: None
) -> None:
    plan = materialize_plan(
        _request(shard_mode="fixed_count", shard_count=1),
        results_root=tmp_path,
    )
    registry = JobRegistry(tmp_path / "dashboard.sqlite3")
    job = registry.create(plan)
    error = (
        "Traceback...\n"
        "ValueError: dataset requires unavailable evaluators: ['rubric_judge']; "
        "token=super-secret"
    )
    plan["tasks"][0]["status"] = "failed"
    plan["tasks"][0]["error"] = error
    Path(job.plan_path).write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8"
    )
    registry.update(job.id, status="failed", error="task failed")
    manager = object.__new__(JobManager)
    manager.registry = registry
    manager.results_root = tmp_path

    diagnostics = manager.diagnostics(job.id)

    failed = diagnostics["failed_tasks"][0]
    assert failed["id"] == "shard-001-dense-rag"
    assert "rubric_judge" in failed["root_error"]
    assert "super-secret" not in failed["traceback"]
    assert "bağımsız judge" in failed["resolution"]
    assert diagnostics["artifacts"]["plan"].endswith("/plan.json")


def test_native_profile_enables_real_mesa_and_mem0_ingest_modes(
    tmp_path: Path, available_test_clients: None
) -> None:
    plan = materialize_plan(
        _request(profile="native", clients=["mesa", "mem0"]),
        results_root=tmp_path,
    )
    configs = {
        task["client"]: yaml.safe_load(Path(task["config"]).read_text(encoding="utf-8"))
        for task in plan["tasks"]
        if task["shard_id"] == "shard-001"
    }
    assert configs["mesa"]["client"]["parameters"]["native_ingest"] is True
    assert configs["mesa"]["client"]["parameters"]["enable_multi_hop"] is True
    assert configs["mem0"]["client"]["parameters"]["infer"] is True


def test_progress_sink_emits_and_honors_safe_control(tmp_path: Path) -> None:
    event_file = tmp_path / "events.jsonl"
    control_file = tmp_path / "control.json"
    sink = ProgressSink("run-1", event_file=event_file, control_file=control_file)
    sink.emit("setup", "started", scenario_total=2)
    row = json.loads(event_file.read_text(encoding="utf-8"))
    assert row["phase"] == "setup"
    assert row["sequence"] == 1
    control_file.write_text('{"action":"pause"}', encoding="utf-8")
    try:
        sink.check_control()
    except BenchmarkControlRequested as exc:
        assert exc.action == "pause"
    else:
        raise AssertionError("pause control was not raised")


def test_runner_pause_resume_preserves_exact_question_coverage(
    tmp_path: Path,
) -> None:
    control_file = tmp_path / "control.json"
    event_file = tmp_path / "events.jsonl"
    control_file.write_text('{"action":"pause"}', encoding="utf-8")
    first = BenchmarkRunner(
        "resource://configs/internal/smoke_dense.yaml",
        results_root=tmp_path / "results",
        event_file=event_file,
        control_file=control_file,
    ).run()
    assert first["status"] == "paused"

    control_file.write_text("{}", encoding="utf-8")
    second = BenchmarkRunner(
        "resource://configs/internal/smoke_dense.yaml",
        results_root=tmp_path / "results",
        event_file=event_file,
        control_file=control_file,
    ).run()
    rows = [
        json.loads(line)
        for line in Path(second["results_file"])
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    keys = {(row["iteration"], row["scenario_id"], row["question_id"]) for row in rows}
    assert len(rows) == len(keys) == 2
    assert second["metrics"]["valid"] is True


async def test_dashboard_api_is_local_control_surface(
    tmp_path: Path, available_test_clients: None
) -> None:
    app = create_dashboard_app(results_root=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/api/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "local_only": True}
        catalog = await client.get("/api/catalog")
        assert catalog.status_code == 200
        assert {item["id"] for item in catalog.json()["clients"]} == {
            "mesa",
            "dense-rag",
            "mem0",
            "letta",
            "zep",
        }
        preview = await client.post("/api/plans/preview", json=_request().model_dump())
        assert preview.status_code == 200
        assert preview.json()["ready"] is True
        datasets = await client.get("/api/datasets")
        assert datasets.status_code == 200
        smoke = next(item for item in datasets.json() if item["id"] == "smoke")
        assert smoke["ready"] is True
        scenarios = await client.get("/api/datasets/smoke/scenarios?limit=1")
        assert scenarios.status_code == 200
        assert scenarios.json()["items"][0]["questions"]

        rejected = await client.put(
            "/api/settings/ollama",
            json={
                "url": "http://" + "user" + ":secret@" + "127.0.0.1:11434",
                "model": "qwen",
            },
        )
        assert rejected.status_code == 422
        saved = await client.put(
            "/api/settings/ollama",
            json={"url": "http://192.168.1.103:11434", "model": "qwen3:8b"},
        )
        assert saved.status_code == 200
