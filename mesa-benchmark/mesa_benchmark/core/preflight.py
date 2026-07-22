import hashlib
import json
from pathlib import Path
from typing import Any

from ..datasets.loader import DatasetManager
from .config import BenchmarkConfig, apply_runtime_environment, load_config


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
        errors.append("evaluation.enable_agreement=true requires a configured judge")

    if errors:
        raise ValueError("Invalid live benchmark configuration: " + "; ".join(errors))


def validate_config(path: str | Path) -> dict[str, Any]:
    config = load_config(path)
    apply_runtime_environment(config)
    validate_live_execution_contract(config)
    return {
        "config": str(path),
        "client": config.client.name,
        "generator_model": config.generation.model,
        "judge_model": config.evaluation.llm_judge_model,
        "multi_judge_models": config.evaluation.multi_judge_models,
        "seed": config.seed,
        "top_k": config.runtime.top_k,
    }


def validate_config_and_dataset(path: str | Path) -> dict[str, Any]:
    config = load_config(path)
    apply_runtime_environment(config)
    manager = DatasetManager(config.dataset.path, config.dataset.noise_ratio)
    manager.load()
    if not manager.scenarios:
        raise ValueError("dataset must contain at least one scenario")
    scenario_ids: set[str] = set()
    question_ids: set[str] = set()
    unresolved_relations: list[str] = []
    for scenario in manager.scenarios:
        if scenario.id in scenario_ids:
            raise ValueError(f"duplicate scenario id: {scenario.id}")
        scenario_ids.add(scenario.id)
        context_ids = {context.id for context in scenario.contexts}
        entity_names = {
            str(context.metadata.get("entity_name"))
            for context in scenario.contexts
            if context.metadata.get("entity_name")
        }
        for context in scenario.contexts:
            for relation in context.metadata.get("relations", []):
                if relation.get("target") not in entity_names:
                    unresolved_relations.append(
                        f"{scenario.id}:{context.id}:{relation.get('target')}"
                    )
        for question in scenario.questions:
            scoped_id = f"{scenario.id}:{question.id}"
            if scoped_id in question_ids:
                raise ValueError(f"duplicate question id in scenario: {scoped_id}")
            question_ids.add(scoped_id)
            missing_contexts = set(question.expected_context_ids).difference(
                context_ids
            )
            if missing_contexts:
                raise ValueError(
                    f"question {scoped_id} references missing contexts: "
                    f"{sorted(missing_contexts)}"
                )
    if unresolved_relations:
        raise ValueError(f"unresolved graph relations: {unresolved_relations[:10]}")
    questions = sum(len(item.questions) for item in manager.scenarios)
    retrieval_evaluable = sum(
        bool(question.expected_context_ids)
        for item in manager.scenarios
        for question in item.questions
    )
    return {
        "config": str(path),
        "dataset": config.dataset.path,
        "dataset_sha256": file_sha256(config.dataset.path),
        "scenarios": len(manager),
        "questions": questions,
        "retrieval_evaluable_questions": retrieval_evaluable,
        "retrieval_metrics_supported": retrieval_evaluable > 0,
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
