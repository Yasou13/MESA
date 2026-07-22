import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
from mesa_benchmark.clients.base import BenchmarkResponse
from mesa_benchmark.clients.dense_rag_client import DenseRagClientAdapter
from mesa_benchmark.core.paths import resolve_benchmark_path
from mesa_benchmark.core.preflight import validate_config_and_dataset
from mesa_benchmark.core.runner import BenchmarkRunner
from mesa_benchmark.core.suite import verify_results
from mesa_benchmark.datasets.manifest import DatasetManifest, validate_dataset_manifest
from mesa_benchmark.datasets.schemas import BenchmarkQuestion
from mesa_benchmark.evaluators.llm_judge import LLMJudgeEvaluator
from mesa_benchmark.evaluators.recall_at_k import RecallAtKEvaluator
from mesa_benchmark.metrics.calculator import (
    MetricsEngine,
    calculate_metrics_from_jsonl,
)
from pydantic import ValidationError


def _load_script(name: str) -> ModuleType:
    path = Path("mesa-benchmark/scripts") / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_question_aliases_are_normalized() -> None:
    question = BenchmarkQuestion(
        id="q1",
        query="Where?",
        ground_truth="Ankara",
        expected_context_ids=["c1"],
    )

    assert question.reference_answers == ["Ankara"]
    assert question.supporting_context_ids == ["c1"]


def test_question_rejects_empty_reference_and_rubric() -> None:
    with pytest.raises(ValidationError, match="reference answer or rubric"):
        BenchmarkQuestion(id="q1", query="Unscoreable", ground_truth="")


def test_rubric_only_question_is_valid_and_preserved() -> None:
    question = BenchmarkQuestion(
        id="beam-q",
        query="Summarize the preference",
        rubric=["Mentions the user's preference for quiet hotels"],
        category="Preference",
        difficulty="hard",
        evaluation_strategy="rubric_judge",
    )

    assert question.reference_answers == []
    assert question.rubric == ["Mentions the user's preference for quiet hotels"]
    assert question.category == "Preference"


def test_legacy_beam_metadata_is_lifted_without_loss() -> None:
    question = BenchmarkQuestion(
        id="beam-q",
        query="What changed?",
        ground_truth="",
        evaluation_strategy="llm_judge",
        metadata={
            "rubric": "Must identify the updated preference",
            "category": "Knowledge Update",
            "difficulty": "medium",
        },
    )

    assert question.rubric == ["Must identify the updated preference"]
    assert question.category == "Knowledge Update"
    assert question.difficulty == "medium"


def test_judge_prompt_contains_question_references_and_rubric() -> None:
    evaluator = LLMJudgeEvaluator(ensemble_size=1, quorum=1)
    question = BenchmarkQuestion(
        id="q",
        query="Which city was selected?",
        reference_answers=["Ankara", "Ankara, Turkey"],
        rubric=["The selected city must be explicit"],
        evaluation_strategy="rubric_judge",
    )
    response = BenchmarkResponse(answer_text="Ankara", latency_ms=1)
    captured: list[str] = []

    def judge(prompt: str) -> dict[str, object]:
        captured.append(prompt)
        return {"is_correct": True, "score": 1.0, "reasoning": "ok"}

    with patch.object(evaluator, "_call_litellm", side_effect=judge):
        evaluator.evaluate(response, question)

    assert "Which city was selected?" in captured[0]
    assert "Ankara, Turkey" in captured[0]
    assert "The selected city must be explicit" in captured[0]


def test_outdated_only_retrieval_is_not_an_authoritative_hit() -> None:
    retrieved = ["old"]
    assert (
        MetricsEngine.calculate_authoritative_hit_at_k(["current"], retrieved, 5) == 0
    )
    assert MetricsEngine.calculate_forbidden_rate_at_k(["old"], retrieved, 5) == 1.0


def test_secondary_top_k_sweep_reports_hit_at_ten_and_twenty(tmp_path: Path) -> None:
    results = tmp_path / "sweep.jsonl"
    results.write_text(
        json.dumps(
            {
                "run_id": "run",
                "iteration": 1,
                "scenario_id": "s",
                "question_id": "q",
                "score": 0.0,
                "is_correct": False,
                "expected_context_ids": ["target"],
                "retrieved_context_ids": [
                    *[f"noise-{index}" for index in range(9)],
                    "target",
                ],
                "infrastructure_error": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    metrics = calculate_metrics_from_jsonl(results)
    assert metrics.hit_at_5 == 0.0
    assert metrics.hit_at_10 == 1.0
    assert metrics.hit_at_20 == 1.0


def test_manifest_requires_license_and_matching_converted_checksum(
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text("[]\n", encoding="utf-8")
    manifest = DatasetManifest(
        schema_version="2.0",
        dataset_name="fixture",
        dataset_version="v2",
        source={"url": "https://example.test/data", "revision": "abc", "split": "test"},
        checksums={"raw_sha256": "1" * 64, "converted_sha256": "0" * 64},
        license={"spdx_id": "MIT", "redistribution": "allowed"},
        designation="external-publishable",
        isolation="scenario",
        ingest_mode="batch",
        chunking={"strategy": "source", "parameters": {}},
        metrics={"supported": ["hit_at_k"], "unsupported": []},
        counts={"scenarios": 0, "contexts": 0, "questions": 0, "categories": {}},
        converter={"version": "1", "parameters": {}},
    )

    with pytest.raises(ValueError, match="converted_sha256"):
        validate_dataset_manifest(manifest, dataset_path, profile="publishable")


def test_beam_release_fixture_has_complete_v2_labels() -> None:
    path = resolve_benchmark_path("data://external/beam/v2/dataset.json")
    rows = json.loads(path.read_text(encoding="utf-8"))
    questions = [question for row in rows for question in row["questions"]]
    assert len(questions) == 400
    parsed = [BenchmarkQuestion(**question) for question in questions]
    assert len({question.category for question in parsed}) == 10
    assert all(question.reference_answers or question.rubric for question in parsed)


def test_beam_converter_golden_preserves_rubric_category_and_difficulty() -> None:
    converter = _load_script("download_beam.py")
    converted = converter.convert_beam_to_mesa(
        [
            {
                "conversation_id": "beam-golden",
                "chat": [{"id": "c1", "role": "user", "content": "hello"}],
                "probing_questions": {
                    "Preference": [
                        {
                            "question": "What is preferred?",
                            "ideal_response": "Quiet rooms",
                            "rubric": "Must explicitly mention quiet rooms",
                            "difficulty": "hard",
                        }
                    ]
                },
            }
        ],
        "100K",
    )

    question = BenchmarkQuestion.model_validate(converted[0]["questions"][0])
    assert question.reference_answers == ["Quiet rooms"]
    assert question.rubric == ["Must explicitly mention quiet rooms"]
    assert question.category == "Preference"
    assert question.difficulty == "hard"


def test_locomo_converter_rejects_undocumented_missing_evidence() -> None:
    converter = _load_script("download_locomo.py")
    raw = [
        {
            "sample_id": "fixture",
            "conversation": {
                "session_1": [{"dia_id": "D1:1", "speaker": "A", "text": "hello"}],
                "session_1_date_time": "2026-01-01",
            },
            "qa": [
                {
                    "question": "Where?",
                    "answer": "Ankara",
                    "category": 1,
                    "evidence": ["D9:9"],
                }
            ],
        }
    ]

    with pytest.raises(ValueError, match="undocumented missing evidence"):
        converter.convert_locomo_to_mesa(raw)


def test_longmemeval_sync_rejects_raw_checksum_mismatch(tmp_path: Path) -> None:
    converter = _load_script("download_longmemeval.py")
    raw = tmp_path / "raw.json"
    raw.write_text("[]\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        converter.acquire_raw(tmp_path / "cache", raw)


def test_memoryagentbench_converter_golden_preserves_official_semantics() -> None:
    converter = _load_script("download_memoryagentbench.py")
    converted = converter.convert(
        [
            {
                "context": "The final project code is MESA-42.",
                "questions": ["What is the final project code?"],
                "answers": [["MESA-42"]],
                "metadata": {
                    "source": "eventqa_131072",
                    "qa_pair_ids": ["official-q1"],
                },
            }
        ],
        512,
    )

    question = BenchmarkQuestion.model_validate(converted[0]["questions"][0])
    assert question.reference_answers == ["MESA-42"]
    assert question.category == "accurate_retrieval"
    assert question.evaluation_strategy == "substring_match"
    assert question.metadata["official_question_id"] == "official-q1"


def test_memoryagentbench_recsys_uses_official_recall_at_five() -> None:
    converter = _load_script("download_memoryagentbench.py")
    converted = converter.convert(
        [
            {
                "context": "A user discussed movie preferences.",
                "questions": ["Recommend movies"],
                "answers": [["23561", "18170"]],
                "metadata": {
                    "source": "recsys_redial_full",
                    "qa_pair_ids": ["recsys-q1"],
                },
            }
        ],
        512,
    )
    question = BenchmarkQuestion.model_validate(converted[0]["questions"][0])
    result = RecallAtKEvaluator(k=5).evaluate(
        BenchmarkResponse(answer_text="Try item 23561 and 7008", latency_ms=1),
        question,
    )

    assert question.category == "recommendation"
    assert question.evaluation_strategy == "recall_at_5"
    assert result.score == 0.5


def test_internal_holdout_is_frozen_balanced_and_bilingual() -> None:
    rows = json.loads(
        resolve_benchmark_path(
            "resource://fixtures/internal/internal_holdout_600.json"
        ).read_text(encoding="utf-8")
    )
    questions = [question for row in rows for question in row["questions"]]
    category_counts: dict[str, int] = {}
    language_counts: dict[str, int] = {}
    normalized_queries: set[str] = set()
    for raw in questions:
        question = BenchmarkQuestion.model_validate(raw)
        category = question.category or "uncategorized"
        language = str(question.metadata["language"])
        category_counts[category] = category_counts.get(category, 0) + 1
        language_counts[language] = language_counts.get(language, 0) + 1
        normalized_queries.add(" ".join(question.query.casefold().split()))

    assert len(questions) == 600
    assert set(category_counts.values()) == {100}
    assert language_counts == {"tr": 120, "en": 480}
    assert len(normalized_queries) >= 570  # normalized duplicate budget <= 5%


def test_beam_capacity_generator_is_opt_in_and_non_retrieval_scored() -> None:
    generator = _load_script("generate_beam_capacity.py")
    source = [
        {
            "id": "s",
            "contexts": [{"id": "c", "text": "one two three four five"}],
            "questions": [
                {
                    "id": "q",
                    "query": "What?",
                    "reference_answers": ["answer"],
                    "rubric": ["Must answer"],
                    "category": "summarization",
                    "evaluation_strategy": "rubric_judge",
                }
            ],
        }
    ]
    converted, actual_tokens = generator.build_capacity(source, target_tokens=2)
    question = BenchmarkQuestion.model_validate(converted[0]["questions"][0])

    assert actual_tokens >= 2
    assert question.supporting_context_ids == []
    assert question.required_context_groups == []
    assert question.forbidden_context_ids == []


def test_beam_common_chunk_ablation_uses_overlap_contract() -> None:
    generator = _load_script("generate_beam_chunk_ablation.py")
    chunks = generator.rechunk_text("one two three four five six", 4, 1)

    assert len(chunks) >= 2
    with pytest.raises(ValueError, match="overlap"):
        generator.rechunk_text("invalid", 4, 4)


def _write_manifest(
    path: Path, dataset: Path, *, designation: str = "internal-regression"
) -> None:
    rows = json.loads(dataset.read_text(encoding="utf-8"))
    questions = [question for row in rows for question in row["questions"]]
    categories: dict[str, int] = {}
    for question in questions:
        category = question.get("category") or "uncategorized"
        categories[category] = categories.get(category, 0) + 1
    raw = {
        "schema_version": "2.0",
        "dataset_name": "fixture",
        "dataset_version": "v2",
        "source": {
            "url": (
                "https://example.test/fixture"
                if designation.startswith("external-")
                else "repository://fixture"
            ),
            "revision": "abc",
            "split": "test",
        },
        "checksums": {
            "raw_sha256": "1" * 64 if designation.startswith("external-") else None,
            "converted_sha256": __import__("hashlib")
            .sha256(dataset.read_bytes())
            .hexdigest(),
        },
        "license": {"spdx_id": "MIT", "redistribution": "allowed"},
        "designation": designation,
        "isolation": "scenario",
        "ingest_mode": "batch",
        "chunking": {"strategy": "source", "parameters": {}},
        "metrics": {"supported": ["hit_at_k"], "unsupported": []},
        "counts": {
            "scenarios": len(rows),
            "contexts": sum(len(row["contexts"]) for row in rows),
            "questions": len(questions),
            "categories": categories,
        },
        "converter": {"version": "test", "parameters": {}},
        "quality": {"normalized_duplicate_query_budget": 1.0},
        "known_annotation_exceptions": [],
    }
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_runner_purges_between_scenarios_when_manifest_requires_isolation(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "id": f"s{index}",
                    "name": f"scenario {index}",
                    "contexts": [{"id": f"c{index}", "text": f"answer-{index}"}],
                    "questions": [
                        {
                            "id": f"q{index}",
                            "query": f"answer-{index}",
                            "ground_truth": f"answer-{index}",
                            "expected_context_ids": [f"c{index}"],
                        }
                    ],
                }
                for index in range(2)
            ]
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, dataset)
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""suite_name: isolation-test
iterations: 1
seed: 42
dataset:
  name: fixture
  version: v2
  path: {dataset}
  manifest_path: {manifest}
  isolation: scenario
  ingest_mode: batch
client:
  name: dense-rag
  adapter_class: mesa_benchmark.clients.dense_rag_client.DenseRagClientAdapter
  parameters:
    embedding_backend: deterministic-hashing
    embedding_model: sha256-hashing-v1
evaluation:
  metrics: [hit_at_k]
  enable_agreement: false
generation:
  enabled: false
runtime:
  top_k: 5
  require_independent_judge: false
""",
        encoding="utf-8",
    )
    original = DenseRagClientAdapter.clear_memory
    calls: list[int] = []

    def tracked_clear(client: DenseRagClientAdapter) -> None:
        calls.append(1)
        original(client)

    with patch.object(DenseRagClientAdapter, "clear_memory", tracked_clear):
        BenchmarkRunner(config, results_root=tmp_path / "results").run()
    assert len(calls) >= 3  # iteration purge + one purge per scenario


def test_publishable_dataset_rejects_mesa_only_graph_metadata(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "id": "s",
                    "name": "s",
                    "contexts": [
                        {
                            "id": "c",
                            "text": "answer",
                            "metadata": {"node_id": "mesa-only"},
                        }
                    ],
                    "questions": [
                        {
                            "id": "q",
                            "query": "q",
                            "ground_truth": "answer",
                            "expected_context_ids": ["c"],
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, dataset, designation="external-publishable")
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""suite_name: graph-leak
iterations: 1
dataset:
  name: fixture
  path: {dataset}
  manifest_path: {manifest}
client:
  name: dummy
  adapter_class: mesa_benchmark.clients.dummy_client.DummyClientAdapter
evaluation:
  enable_agreement: false
generation:
  enabled: false
runtime:
  require_independent_judge: false
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="MESA-only graph metadata"):
        validate_config_and_dataset(config, profile="publishable")


def test_result_verifier_rejects_adapter_chunk_hash_mismatch(tmp_path: Path) -> None:
    manifests = []
    results = []
    for index, chunk_hash in enumerate(("a", "b")):
        manifest = tmp_path / f"manifest-{index}.json"
        manifest.write_text(
            json.dumps(
                {
                    "dataset_sha256": "d",
                    "dataset_designation": "internal-regression",
                    "dataset_counts": {"questions": 1},
                    "iterations": 1,
                    "generator_model": None,
                    "judge_model": None,
                    "multi_judge_models": [],
                    "embedding_model": "same",
                    "top_k": 5,
                    "context_token_budget": 4096,
                    "chunking": {"strategy": "source"},
                    "isolation": "scenario",
                    "ingest_mode": "batch",
                }
            ),
            encoding="utf-8",
        )
        result = tmp_path / f"results-{index}.jsonl"
        result.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "iteration": 1,
                    "scenario_id": "s",
                    "question_id": "q",
                    "score": 1.0,
                    "is_correct": True,
                    "infrastructure_error": False,
                    "input_context_ids": ["c"],
                    "chunk_hashes": [chunk_hash],
                    "top_k": 5,
                    "context_token_budget": 4096,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        manifests.append(manifest)
        results.append(result)
    bundle = tmp_path / "bundle.json"
    bundle.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "profile": "internal",
                "runs": [
                    {
                        "id": f"r{i}",
                        "group": "g",
                        "system": f"sys{i}",
                        "manifest": str(manifests[i]),
                        "results": str(results[i]),
                    }
                    for i in range(2)
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="adapter parity mismatch"):
        verify_results(bundle)


def test_publishable_verifier_requires_judge_execution_not_only_config(
    tmp_path: Path,
) -> None:
    runs = []
    for index, system in enumerate(("mesa", "dense-rag", "mem0")):
        manifest = tmp_path / f"manifest-publishable-{index}.json"
        manifest.write_text(
            json.dumps(
                {
                    "dataset_sha256": "same-dataset",
                    "dataset_designation": "external-publishable",
                    "dataset_license": {
                        "spdx_id": "MIT",
                        "redistribution": "allowed",
                    },
                    "dataset_counts": {"questions": 1},
                    "iterations": 1,
                    "generator_model": "generator-v1",
                    "judge_model": "judge-v1",
                    "multi_judge_models": [],
                    "judge_evaluations": 0,
                    "evidence_tier": "provisional/self-judged",
                    "embedding_model": "same-embedding",
                    "top_k": 5,
                    "track": "safe-core",
                    "context_token_budget": 4096,
                    "chunking": {"strategy": "source"},
                    "isolation": "scenario",
                    "ingest_mode": "batch",
                }
            ),
            encoding="utf-8",
        )
        result = tmp_path / f"results-publishable-{index}.jsonl"
        result.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "iteration": 1,
                    "scenario_id": "s",
                    "question_id": "q",
                    "score": 1.0,
                    "is_correct": True,
                    "infrastructure_error": False,
                    "judge_quorum_met": True,
                    "input_context_ids": ["c"],
                    "chunk_hashes": ["hash"],
                    "top_k": 5,
                    "context_token_budget": 4096,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        runs.append(
            {
                "id": f"run-{index}",
                "group": "external",
                "system": system,
                "manifest": str(manifest),
                "results": str(result),
            }
        )
    calibration = tmp_path / "calibration.json"
    calibration.write_text(
        json.dumps(
            {
                "sample_size": 100,
                "cohens_kappa": 0.8,
                "category_counts": {f"category-{index}": 20 for index in range(5)},
            }
        ),
        encoding="utf-8",
    )
    bundle = tmp_path / "publishable-bundle.json"
    bundle.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "profile": "publishable",
                "judge_calibration_path": str(calibration),
                "runs": runs,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="independent judge did not evaluate"):
        verify_results(bundle)
