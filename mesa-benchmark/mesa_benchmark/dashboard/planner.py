from __future__ import annotations

import hashlib
import json
import math
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..core.paths import resolve_config_path, resolve_results_root
from ..datasets.manifest import load_dataset_manifest
from .catalog import CLIENTS, client_catalog
from .models import PlanRequest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scenario_cost(item: dict[str, Any], q_limit: int, c_limit: int) -> float:
    return (
        len(item.get("questions", [])) / q_limit
        + len(item.get("contexts", [])) / c_limit
    )


def _balanced_shards(
    scenarios: list[dict[str, Any]],
    question_limit: int,
    context_limit: int,
    *,
    shard_count: int | None = None,
) -> list[list[dict[str, Any]]]:
    total_questions = sum(len(item.get("questions", [])) for item in scenarios)
    total_contexts = sum(len(item.get("contexts", [])) for item in scenarios)
    if shard_count is None:
        shard_count = max(
            1,
            math.ceil(total_questions / question_limit),
            math.ceil(total_contexts / context_limit),
        )
    if shard_count > len(scenarios):
        raise ValueError("Shard sayısı scenario sayısından büyük olamaz")
    bins: list[list[tuple[int, dict[str, Any]]]] = [[] for _ in range(shard_count)]
    loads = [0.0 for _ in range(shard_count)]
    ordered = sorted(
        enumerate(scenarios),
        key=lambda pair: (
            -_scenario_cost(pair[1], question_limit, context_limit),
            str(pair[1].get("id", "")),
        ),
    )
    for original_index, scenario in ordered:
        target = min(range(shard_count), key=lambda index: (loads[index], index))
        bins[target].append((original_index, scenario))
        loads[target] += _scenario_cost(scenario, question_limit, context_limit)
    return [
        [item for _, item in sorted(bucket, key=lambda pair: pair[0])]
        for bucket in bins
        if bucket
    ]


def _categories(scenarios: list[dict[str, Any]]) -> dict[str, int]:
    values: dict[str, int] = {}
    for scenario in scenarios:
        for question in scenario.get("questions", []):
            category = str(question.get("category") or "uncategorized")
            values[category] = values.get(category, 0) + 1
    return values


def _requested_shard_count(
    request: PlanRequest, scenarios: list[dict[str, Any]]
) -> int | None:
    if request.shard_mode == "fixed_count":
        return request.shard_count or 1
    return None


def _requested_limits(
    request: PlanRequest, seconds_per_question: float | None
) -> tuple[int, int]:
    if request.shard_mode == "auto_duration" and seconds_per_question:
        question_limit = max(
            1,
            int(
                request.target_shard_minutes
                * 60
                / seconds_per_question
                / request.iterations
            ),
        )
        return question_limit, request.shard_context_limit
    return request.shard_question_limit, request.shard_context_limit


def preview_plan(
    request: PlanRequest,
    *,
    ollama_configured: bool = False,
    default_model: str | None = None,
    seconds_per_question: float | None = None,
    history_samples: int = 0,
) -> dict[str, Any]:
    config_path = resolve_config_path(request.config)
    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    dataset_path = Path(raw_config["dataset"]["path"])
    if not dataset_path.is_absolute():
        from ..core.paths import resolve_benchmark_path

        dataset_path = resolve_benchmark_path(
            raw_config["dataset"]["path"], base_dir=config_path.parent
        )
    scenarios = json.loads(dataset_path.read_text(encoding="utf-8"))
    required_evaluators = {
        str(question.get("evaluation_strategy") or "exact_match")
        for scenario in scenarios
        for question in scenario.get("questions", [])
    }
    question_limit, context_limit = _requested_limits(request, seconds_per_question)
    shards = _balanced_shards(
        scenarios,
        question_limit,
        context_limit,
        shard_count=_requested_shard_count(request, scenarios),
    )
    available = {item["id"]: item for item in client_catalog()}
    blockers = [
        f"{client_id}: {available[client_id]['reason']}"
        for client_id in request.clients
        if not available[client_id]["available"]
    ]
    if request.profile == "native":
        unsupported = [
            client_id
            for client_id in request.clients
            if not available[client_id]["native_mode"]
        ]
        if unsupported:
            blockers.append(
                "Native Memory adapterı henüz doğrulanmamış client’lar: "
                + ", ".join(unsupported)
            )
    generation_enabled = (
        bool(raw_config.get("generation", {}).get("enabled"))
        if request.generation_enabled is None
        else request.generation_enabled
    )
    if request.profile == "capacity":
        generation_enabled = False
    generator_model = request.generator_model or default_model
    if request.judge_enabled and not request.judge_model:
        blockers.append("Judge etkinse bağımsız judge modeli seçilmelidir")
    semantic_evaluators = required_evaluators.intersection(
        {"llm_judge", "multi_model_judge", "rubric_judge"}
    )
    if semantic_evaluators and not request.judge_enabled:
        blockers.append(
            "Dataset semantik evaluator gerektiriyor: "
            + ", ".join(sorted(semantic_evaluators))
            + ". Bağımsız judge'ı etkinleştirin."
        )
    if semantic_evaluators and request.profile == "capacity":
        blockers.append(
            "Bu dataset Capacity profiliyle kullanılamaz; evaluator için judge gerekir"
        )
    if (
        request.judge_enabled
        and request.judge_model
        and generator_model
        and request.judge_model == generator_model
    ):
        blockers.append("Generator ve judge aynı model olamaz")
    requires_ollama = (
        request.profile == "native" or generation_enabled or request.judge_enabled
    )
    if requires_ollama and not ollama_configured:
        blockers.append("Bu plan Ollama bağlantısı gerektiriyor")
    if generation_enabled and not generator_model:
        blockers.append("Generation için model seçilmelidir")
    return {
        "name": request.name,
        "profile": request.profile,
        "dataset": {
            "name": raw_config["dataset"]["name"],
            "version": raw_config["dataset"]["version"],
            "path": str(dataset_path),
            "scenarios": len(scenarios),
            "questions": sum(len(item.get("questions", [])) for item in scenarios),
            "contexts": sum(len(item.get("contexts", [])) for item in scenarios),
        },
        "clients": request.clients,
        "shards": [
            {
                "index": index,
                "scenarios": len(items),
                "questions": sum(len(item.get("questions", [])) for item in items),
                "contexts": sum(len(item.get("contexts", [])) for item in items),
                "scenario_ids": [str(item["id"]) for item in items],
                "estimated_seconds": (
                    sum(len(item.get("questions", [])) for item in items)
                    * request.iterations
                    * seconds_per_question
                    if seconds_per_question
                    else None
                ),
            }
            for index, items in enumerate(shards, start=1)
        ],
        "tasks": len(shards) * len(request.clients),
        "shard_mode": request.shard_mode,
        "target_shard_minutes": request.target_shard_minutes,
        "estimated_total_seconds": (
            int(
                sum(len(item.get("questions", [])) for item in scenarios)
                * request.iterations
                * len(request.clients)
                * seconds_per_question
            )
            if seconds_per_question
            else None
        ),
        "eta_confidence": (
            "yüksek"
            if history_samples >= 5
            else "orta" if history_samples >= 2 else "düşük"
        ),
        "requires_ollama": requires_ollama,
        "generator_model": generator_model,
        "required_evaluators": sorted(required_evaluators),
        "blockers": blockers,
        "ready": not blockers,
    }


def materialize_plan(
    request: PlanRequest,
    *,
    results_root: str | Path | None = None,
    ollama_configured: bool = True,
    default_model: str | None = None,
    seconds_per_question: float | None = None,
    history_samples: int = 0,
) -> dict[str, Any]:
    preview = preview_plan(
        request,
        ollama_configured=ollama_configured,
        default_model=default_model,
        seconds_per_question=seconds_per_question,
        history_samples=history_samples,
    )
    if not preview["ready"]:
        raise ValueError("; ".join(preview["blockers"]))
    job_id = str(uuid.uuid4())
    root = resolve_results_root(results_root) / "dashboard" / job_id
    root.mkdir(parents=True, exist_ok=False)
    (root / "shards").mkdir()
    (root / "configs").mkdir()
    (root / "runs").mkdir()

    source_config_path = resolve_config_path(request.config)
    source_config = yaml.safe_load(source_config_path.read_text(encoding="utf-8"))
    source_dataset = Path(preview["dataset"]["path"])
    scenarios = json.loads(source_dataset.read_text(encoding="utf-8"))
    question_limit, context_limit = _requested_limits(request, seconds_per_question)
    shards = _balanced_shards(
        scenarios,
        question_limit,
        context_limit,
        shard_count=_requested_shard_count(request, scenarios),
    )
    source_manifest_path = source_config["dataset"].get("manifest_path")
    if not source_manifest_path:
        raise ValueError("dashboard sharding requires a dataset manifest")
    from ..core.paths import resolve_benchmark_path

    resolved_manifest = resolve_benchmark_path(
        source_manifest_path, base_dir=source_config_path.parent
    )
    source_manifest = load_dataset_manifest(resolved_manifest).model_dump(mode="json")
    tasks: list[dict[str, Any]] = []
    shard_entries: list[dict[str, Any]] = []

    for shard_index, shard in enumerate(shards, start=1):
        shard_id = f"shard-{shard_index:03d}"
        dataset_file = root / "shards" / f"{shard_id}.json"
        dataset_file.write_text(
            json.dumps(shard, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest = deepcopy(source_manifest)
        manifest["dataset_version"] = f"{source_manifest['dataset_version']}-{shard_id}"
        manifest["checksums"]["converted_sha256"] = _sha256(dataset_file)
        manifest["counts"] = {
            "scenarios": len(shard),
            "contexts": sum(len(item.get("contexts", [])) for item in shard),
            "questions": sum(len(item.get("questions", [])) for item in shard),
            "categories": _categories(shard),
        }
        converter_parameters = dict(manifest["converter"].get("parameters") or {})
        converter_parameters.update(
            {
                "dashboard_shard": shard_index,
                "dashboard_total_shards": len(shards),
                "source_converted_sha256": source_manifest["checksums"][
                    "converted_sha256"
                ],
            }
        )
        manifest["converter"]["parameters"] = converter_parameters
        manifest_file = root / "shards" / f"{shard_id}-manifest.json"
        manifest_file.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        shard_entry = {
            "id": shard_id,
            "index": shard_index,
            "dataset": str(dataset_file),
            "manifest": str(manifest_file),
            "scenarios": len(shard),
            "contexts": manifest["counts"]["contexts"],
            "questions": manifest["counts"]["questions"],
            "scenario_ids": [str(item["id"]) for item in shard],
        }
        shard_entries.append(shard_entry)
        for client_id in request.clients:
            spec = CLIENTS[client_id]
            config = deepcopy(source_config)
            config["suite_name"] = f"{request.name} · {shard_id}"
            config["seed"] = request.seed
            config["iterations"] = request.iterations
            config["dataset"]["path"] = str(dataset_file)
            config["dataset"]["manifest_path"] = str(manifest_file)
            config["dataset"]["version"] = manifest["dataset_version"]
            parameters = deepcopy(spec["parameters"])
            if client_id == "mem0":
                parameters["infer"] = request.profile == "native"
            if client_id == "mesa" and request.profile == "native":
                parameters["native_ingest"] = True
                parameters["enable_multi_hop"] = True
            config["client"] = {
                "name": client_id,
                "adapter_class": spec["adapter_class"],
                "timeout_ms": 120_000,
                "parameters": parameters,
            }
            generation = config.setdefault("generation", {})
            generation_enabled = (
                bool(generation.get("enabled"))
                if request.generation_enabled is None
                else request.generation_enabled
            )
            if request.profile == "capacity":
                generation_enabled = False
            generation["enabled"] = generation_enabled
            generation["temperature"] = request.generation_temperature
            selected_model = request.generator_model or default_model
            if selected_model:
                generation["model"] = selected_model
            evaluation = config.setdefault("evaluation", {})
            runtime = config.setdefault("runtime", {})
            runtime["top_k"] = request.top_k
            runtime["context_token_budget"] = request.context_token_budget
            if request.judge_enabled:
                evaluation["llm_judge_model"] = request.judge_model
                evaluation["multi_judge_models"] = []
                evaluation["enable_agreement"] = True
                runtime["require_independent_judge"] = True
            else:
                evaluation["llm_judge_model"] = None
                evaluation["multi_judge_models"] = []
                evaluation["enable_agreement"] = False
                runtime["require_independent_judge"] = False
            if request.profile == "capacity":
                generation["enabled"] = False
                evaluation["enable_agreement"] = False
                runtime["require_independent_judge"] = False
            config_file = root / "configs" / f"{shard_id}-{client_id}.yaml"
            config_file.write_text(
                yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
            )
            tasks.append(
                {
                    "id": f"{shard_id}-{client_id}",
                    "shard_id": shard_id,
                    "client": client_id,
                    "config": str(config_file),
                    "results_root": str(root / "runs" / shard_id),
                    "status": "queued",
                    "attempt": 1,
                }
            )

    plan = {
        "schema_version": 1,
        "id": job_id,
        "name": request.name,
        "profile": request.profile,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": request.seed,
        "source_config": str(source_config_path),
        "source_dataset_sha256": _sha256(source_dataset),
        "clients": request.clients,
        "time_limit_minutes": request.time_limit_minutes,
        "warmup_enabled": request.warmup_enabled and request.profile != "capacity",
        "shard_mode": request.shard_mode,
        "target_shard_minutes": request.target_shard_minutes,
        "estimated_seconds_per_question": seconds_per_question,
        "history_samples": history_samples,
        "shards": shard_entries,
        "tasks": tasks,
        "status": "queued",
        "request": request.model_dump(),
    }
    plan_json = json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True)
    plan["plan_sha256"] = hashlib.sha256(plan_json.encode("utf-8")).hexdigest()
    plan_path = root / "plan.json"
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "events.jsonl").touch()
    (root / "control.json").write_text("{}\n", encoding="utf-8")
    return plan
