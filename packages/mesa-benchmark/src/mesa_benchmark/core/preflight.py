import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..datasets.loader import DatasetManager
from ..datasets.manifest import (
    DatasetManifest,
    load_dataset_manifest,
    validate_dataset_manifest,
)
from .config import BenchmarkConfig, apply_runtime_environment, load_config
from .paths import resolve_config_path


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_live_execution_contract(config: BenchmarkConfig) -> None:
    """Fail fast for an incomplete Full-QA configuration without network access."""
    errors: list[str] = []
    generator = (config.generation.model or "").removeprefix("openai/")
    judges = {
        model.removeprefix("openai/")
        for model in [
            config.evaluation.llm_judge_model or "",
            *config.evaluation.multi_judge_models,
        ]
        if model
    }
    has_supported_judge = bool(config.evaluation.llm_judge_model) or len(judges) >= 2

    if config.generation.enabled:
        if not generator:
            errors.append("generation.enabled=true requires a generator model")
        if not config.runtime.ollama_url:
            errors.append(
                "generation.enabled=true requires runtime.ollama_url or BENCHMARK_OLLAMA_URL"
            )
        if config.runtime.require_independent_judge:
            if not has_supported_judge:
                errors.append(
                    "runtime.require_independent_judge=true requires a configured judge"
                )
            elif generator and not any(judge != generator for judge in judges):
                errors.append(
                    "runtime.require_independent_judge=true requires a judge model different from the generator"
                )

    if config.evaluation.enable_agreement and not has_supported_judge:
        errors.append("enable_agreement requires a configured judge")

    if errors:
        raise ValueError("Invalid live benchmark configuration: " + "; ".join(errors))


def validate_config(path: str | Path) -> dict[str, Any]:
    resolved_config_path = resolve_config_path(path)
    config = load_config(resolved_config_path)
    apply_runtime_environment(config)
    validate_live_execution_contract(config)
    return {
        "config": str(resolved_config_path),
        "client": config.client.name,
        "generator_model": config.generation.model,
        "judge_model": config.evaluation.llm_judge_model,
        "multi_judge_models": config.evaluation.multi_judge_models,
        "seed": config.seed,
        "top_k": config.runtime.top_k,
    }


def _normalized_query(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def _validate_manifest_protocol(
    config: BenchmarkConfig, manifest: DatasetManifest
) -> None:
    if config.dataset.isolation and config.dataset.isolation != manifest.isolation:
        raise ValueError(
            "config cannot override manifest isolation: "
            f"{config.dataset.isolation!r} != {manifest.isolation!r}"
        )
    if (
        config.dataset.ingest_mode
        and config.dataset.ingest_mode != manifest.ingest_mode
    ):
        raise ValueError(
            "config cannot override manifest ingest_mode: "
            f"{config.dataset.ingest_mode!r} != {manifest.ingest_mode!r}"
        )


def validate_config_and_dataset(
    path: str | Path, *, profile: str = "internal"
) -> dict[str, Any]:
    resolved_config_path = resolve_config_path(path)
    config = load_config(resolved_config_path)
    apply_runtime_environment(config)
    manager = DatasetManager(config.dataset.path, config.dataset.noise_ratio)
    manager.load()
    if not manager.scenarios:
        raise ValueError("dataset must contain at least one scenario")
    scenario_ids: set[str] = set()
    question_ids: set[str] = set()
    unresolved_relations: list[str] = []
    mesa_only_metadata: list[str] = []
    categories: dict[str, int] = {}
    contexts = 0
    for scenario in manager.scenarios:
        if scenario.id in scenario_ids:
            raise ValueError(f"duplicate scenario id: {scenario.id}")
        scenario_ids.add(scenario.id)
        contexts += len(scenario.contexts)
        context_ids = {context.id for context in scenario.contexts}
        entity_names = {
            str(context.metadata.get("entity_name"))
            for context in scenario.contexts
            if context.metadata.get("entity_name")
        }
        for context in scenario.contexts:
            forbidden_metadata = {"relations", "edges", "node_id"}.intersection(
                context.metadata
            )
            if forbidden_metadata:
                mesa_only_metadata.append(
                    f"{scenario.id}:{context.id}:{sorted(forbidden_metadata)}"
                )
            for relation in context.metadata.get("relations", []):
                if relation.get("target") not in entity_names:
                    unresolved_relations.append(
                        f"{scenario.id}:{context.id}:{relation.get('target')}"
                    )
        for question in scenario.questions:
            scoped_id = f"{scenario.id}:{question.id}"
            if question.id in question_ids:
                raise ValueError(f"duplicate question id: {question.id}")
            question_ids.add(question.id)
            category = question.category or "uncategorized"
            categories[category] = categories.get(category, 0) + 1
            referenced = set(question.supporting_context_ids)
            referenced.update(question.forbidden_context_ids)
            referenced.update(
                item for group in question.required_context_groups for item in group
            )
            missing_contexts = referenced.difference(context_ids)
            if missing_contexts:
                raise ValueError(
                    f"question {scoped_id} references missing contexts: "
                    f"{sorted(missing_contexts)}"
                )
    if unresolved_relations:
        raise ValueError(f"unresolved graph relations: {unresolved_relations[:10]}")
    questions = sum(len(item.questions) for item in manager.scenarios)
    normalized_queries = [
        _normalized_query(question.query)
        for item in manager.scenarios
        for question in item.questions
    ]
    duplicate_queries = len(normalized_queries) - len(set(normalized_queries))
    duplicate_ratio = duplicate_queries / questions if questions else 0.0
    retrieval_evaluable = sum(
        bool(question.supporting_context_ids or question.required_context_groups)
        for item in manager.scenarios
        for question in item.questions
    )
    manifest_result: dict[str, Any] | None = None
    manifest: DatasetManifest | None = None
    if config.dataset.manifest_path:
        manifest = load_dataset_manifest(config.dataset.manifest_path)
        _validate_manifest_protocol(config, manifest)
        manifest_result = validate_dataset_manifest(
            manifest, manager.dataset_path, profile=profile
        )
        actual_counts = {
            "scenarios": len(manager),
            "contexts": contexts,
            "questions": questions,
            "categories": categories,
        }
        if manifest.counts.model_dump() != actual_counts:
            raise ValueError(
                "dataset counts do not match manifest: "
                f"expected={manifest.counts.model_dump()} actual={actual_counts}"
            )
        if duplicate_ratio > manifest.quality.normalized_duplicate_query_budget:
            raise ValueError(
                "normalized duplicate query budget exceeded: "
                f"actual={duplicate_ratio:.4f} "
                f"budget={manifest.quality.normalized_duplicate_query_budget:.4f}"
            )
        if manifest.designation.startswith("external-") and mesa_only_metadata:
            raise ValueError(
                "external comparison contains MESA-only graph metadata: "
                f"{mesa_only_metadata[:10]}"
            )
    elif profile == "publishable":
        raise ValueError("publishable profile requires dataset.manifest_path")

    return {
        "config": str(resolved_config_path),
        "dataset": config.dataset.path,
        "dataset_sha256": file_sha256(manager.dataset_path),
        "scenarios": len(manager),
        "questions": questions,
        "retrieval_evaluable_questions": retrieval_evaluable,
        "retrieval_metrics_supported": retrieval_evaluable > 0,
        "normalized_duplicate_queries": duplicate_queries,
        "normalized_duplicate_query_ratio": duplicate_ratio,
        "categories": categories,
        "manifest": manifest_result,
    }


def ollama_preflight(config: BenchmarkConfig) -> dict[str, Any]:
    apply_runtime_environment(config)
    validate_live_execution_contract(config)
    import os

    import ollama

    host = os.environ.get("BENCHMARK_OLLAMA_URL", "")
    if not host:
        raise RuntimeError("BENCHMARK_OLLAMA_URL is not configured")
    client = ollama.Client(host=host, timeout=config.generation.timeout_s)
    listed = client.list()
    models = getattr(listed, "models", None)
    if models is None:
        models = listed.get("models", [])
    available = sorted(
        str(
            getattr(item, "model", None)
            or getattr(item, "name", None)
            or item.get("model")
            or item.get("name")
        )
        for item in models
    )
    required = {
        model
        for model in [
            config.generation.model,
            config.evaluation.llm_judge_model,
            *config.evaluation.multi_judge_models,
        ]
        if model
    }
    if config.client.name.lower().startswith("mem0"):
        required.add(
            os.environ.get("BENCHMARK_EMBEDDING_MODEL", "nomic-embed-text:latest")
        )
    normalized_required = {model.removeprefix("openai/") for model in required}
    missing = sorted(normalized_required.difference(available))
    if missing:
        raise RuntimeError(
            f"Required Ollama models are missing: {missing}; available={available}"
        )
    if not normalized_required:
        raise RuntimeError("no generator or judge model is configured for preflight")
    smoke_model = sorted(normalized_required)[0]
    response = client.chat(
        model=smoke_model,
        messages=[{"role": "user", "content": 'Reply with JSON: {"ok": true}'}],
        format={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
        think=False,
        options={"temperature": 0, "seed": config.seed},
    )
    message = getattr(response, "message", None) or response.get("message", {})
    content = getattr(message, "content", None) or message.get("content", "")
    smoke = json.loads(content)
    if smoke.get("ok") is not True:
        raise RuntimeError(f"Ollama JSON smoke test failed: {smoke}")
    return {
        "host": host,
        "available_models": available,
        "required_models": sorted(required),
        "json_smoke_model": smoke_model,
        "json_smoke_ok": True,
    }


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
