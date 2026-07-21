import asyncio
import importlib.util
import json
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mesa_benchmark.clients.base import BenchmarkResponse, RetrievedContext
from mesa_benchmark.clients.mem0_client import Mem0ClientAdapter
from mesa_benchmark.clients.mesa_client import MesaClientAdapter
from mesa_benchmark.core.config import apply_runtime_environment, load_config
from mesa_benchmark.core.generation import OllamaAnswerGenerator
from mesa_benchmark.core.preflight import (
    file_sha256,
    ollama_preflight,
    validate_config,
    validate_config_and_dataset,
)
from mesa_benchmark.core.runner import (
    BenchmarkRunInvalid,
    BenchmarkRunner,
    BenchmarkTimeoutError,
)
from mesa_benchmark.core.state_manager import StateManager
from mesa_benchmark.datasets.schemas import BenchmarkQuestion, MemoryContext
from mesa_benchmark.evaluators.llm_judge import LLMJudgeEvaluator
from mesa_benchmark.evaluators.multi_model_judge import MultiModelJudgeEvaluator
from mesa_benchmark.evaluators.multi_model_judge import _call_litellm as multi_call
from mesa_benchmark.evaluators.qa_metrics import exact_match, token_f1
from mesa_benchmark.evaluators.regex import RegexEvaluator
from mesa_benchmark.evaluators.verdict import parse_judge_verdict
from mesa_benchmark.metrics.calculator import (
    calculate_metrics_from_jsonl,
)
from mesa_benchmark.reports.statistics import (
    compute_paired_test,
    compute_run_statistics,
    compute_t_test_p_value,
)


def test_response_enforces_top_k_for_payload_and_legacy_ids() -> None:
    response = BenchmarkResponse(
        answer_text="raw",
        retrieved_context_ids=[f"c{i}" for i in range(8)],
        retrieved_contexts=[
            RetrievedContext(id=f"c{i}", text=str(i), rank=i + 1) for i in range(8)
        ],
        latency_ms=1,
    ).enforce_top_k(5)
    assert response.retrieved_context_ids == ["c0", "c1", "c2", "c3", "c4"]
    assert len(response.retrieved_contexts) == 5
    assert response.retrieval_latency_ms == 1


def test_full_qa_normalized_em_and_token_f1() -> None:
    assert exact_match("The Ankara!", "ankara") == 1.0
    assert token_f1("Ankara is the capital", "capital Ankara") == pytest.approx(0.8)


def test_multiseed_statistics_include_real_ci_and_paired_delta() -> None:
    summary = compute_run_statistics([0.7, 0.8, 0.9])
    assert summary["mean"] == pytest.approx(0.8)
    assert summary["std"] == pytest.approx(0.1)
    assert summary["ci_95"] > 0
    paired = compute_paired_test([1, 1, 1], [0, 0, 0])
    assert paired["mean_difference"] == 1
    assert paired["is_significant"] is True


def test_statistics_edge_cases_and_welch_comparison() -> None:
    assert compute_run_statistics([])["formatted_str"] == "N/A"
    assert compute_run_statistics([0.5])["ci_95"] == 0
    assert compute_t_test_p_value([1], [0])["is_significant"] is False
    assert compute_t_test_p_value([1, 1, 1], [1, 1, 1])["p_value_approx"] == 1
    assert compute_t_test_p_value([1, 1, 1], [0, 0, 0])["is_significant"] is True
    varied = compute_t_test_p_value([0.8, 0.9, 1.0], [0.1, 0.2, 0.3])
    assert varied["mean_diff"] > 0
    with pytest.raises(ValueError, match="same length"):
        compute_paired_test([1], [1, 2])
    assert compute_paired_test([1], [1])["is_significant"] is False


def test_regex_evaluator_success_and_invalid_pattern() -> None:
    response = BenchmarkResponse(answer_text="Ankara 2026", latency_ms=1)
    question = BenchmarkQuestion(
        id="q", query="q", ground_truth=r"Ankara\s+\d{4}", evaluation_strategy="regex"
    )
    assert RegexEvaluator().evaluate(response, question).is_correct is True
    question.ground_truth = "["
    assert RegexEvaluator().evaluate(response, question).is_correct is False


def test_runner_converts_provider_timeout_without_creating_a_worker_thread() -> None:
    runner = BenchmarkRunner("unused")
    runner.config = cast(Any, SimpleNamespace(client=SimpleNamespace(timeout_ms=20)))
    before = {thread.ident for thread in threading.enumerate()}
    with pytest.raises(BenchmarkTimeoutError):
        runner._call_with_backoff(
            lambda: (_ for _ in ()).throw(TimeoutError("provider deadline"))
        )
    assert {thread.ident for thread in threading.enumerate()} == before


def test_single_judge_uses_boolean_majority_not_average_score() -> None:
    evaluator = LLMJudgeEvaluator(ensemble_size=3, quorum=2)
    responses = iter(
        [
            {"is_correct": True, "score": 0.1, "reasoning": "a"},
            {"is_correct": True, "score": 0.1, "reasoning": "b"},
            {"is_correct": False, "score": 1.0, "reasoning": "c"},
        ]
    )
    question = BenchmarkQuestion(id="q", query="q", ground_truth="g")
    response = BenchmarkResponse(answer_text="a", latency_ms=1)
    with patch.object(
        evaluator, "_call_litellm", side_effect=lambda _: next(responses)
    ):
        result = evaluator.evaluate(response, question)
    assert result.is_correct is True
    assert result.score == pytest.approx(0.4)


def test_multi_judge_requires_distinct_models_and_uses_verdict() -> None:
    with pytest.raises(ValueError, match="distinct"):
        MultiModelJudgeEvaluator(["same", "same"])
    with pytest.raises(ValueError, match="distinct"):
        MultiModelJudgeEvaluator(["same", "openai/same"])
    evaluator = MultiModelJudgeEvaluator(["a", "b", "c"])
    responses = iter(
        [
            {"is_correct": True, "score": 0.1, "reasoning": "a"},
            {"is_correct": True, "score": 0.1, "reasoning": "b"},
            {"is_correct": False, "score": 1.0, "reasoning": "c"},
        ]
    )
    question = BenchmarkQuestion(id="q", query="q", ground_truth="g")
    response = BenchmarkResponse(answer_text="a", latency_ms=1)
    with patch(
        "mesa_benchmark.evaluators.multi_model_judge._call_litellm",
        side_effect=lambda *_: next(responses),
    ):
        result = evaluator.evaluate(response, question)
    assert result.is_correct is True
    assert result.score == pytest.approx(0.4)


def test_multi_judge_runs_models_concurrently_with_bounded_workers() -> None:
    evaluator = MultiModelJudgeEvaluator(["a", "b", "c"], max_concurrency=3)
    question = BenchmarkQuestion(id="q", query="q", ground_truth="g")
    response = BenchmarkResponse(answer_text="a", latency_ms=1)

    def delayed_result(*args: Any) -> dict[str, Any]:
        time.sleep(0.05)
        return {"is_correct": True, "score": 1.0, "reasoning": str(args[0])}

    started = time.monotonic()
    with patch(
        "mesa_benchmark.evaluators.multi_model_judge._call_litellm",
        side_effect=delayed_result,
    ):
        result = evaluator.evaluate(response, question)
    assert time.monotonic() - started < 0.13
    assert result.metadata["models_queried"] == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("raw", "accepted"),
    [
        ('{"is_correct": true, "score": 1.0, "reasoning": "ok"}', True),
        ('```json\n{"is_correct": false, "score": 0.0, "reasoning": "no"}\n```', True),
        ('prose {"is_correct": true, "score": 1.0, "reasoning": "wrong"}', False),
        (
            '{"is_correct": true, "score": 1.0, "reasoning": "one"}\n{"is_correct": false, "score": 0.0, "reasoning": "two"}',
            False,
        ),
        ('{"is_correct": "true", "score": 1.0, "reasoning": "wrong type"}', False),
    ],
)
def test_judge_verdict_parser_accepts_only_one_strict_json_object(
    raw: str, accepted: bool
) -> None:
    if accepted:
        assert parse_judge_verdict(raw).reasoning in {"ok", "no"}
    else:
        with pytest.raises(ValueError):
            parse_judge_verdict(raw)


def test_metrics_exclude_questions_without_expected_ids_from_retrieval_denominator(
    tmp_path: Path,
) -> None:
    path = tmp_path / "results.jsonl"
    rows = [
        {
            "run_id": "r",
            "iteration": 1,
            "scenario_id": "s1",
            "question_id": "q1",
            "score": 1,
            "is_correct": True,
            "latency_ms": 2,
            "expected_context_ids": ["c1"],
            "retrieved_context_ids": ["c1"],
        },
        {
            "run_id": "r",
            "iteration": 1,
            "scenario_id": "s2",
            "question_id": "q2",
            "score": 0,
            "is_correct": False,
            "latency_ms": 3,
            "expected_context_ids": [],
            "retrieved_context_ids": [],
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    metrics = calculate_metrics_from_jsonl(path)
    assert metrics.retrieval_evaluable_questions == 1
    assert metrics.hit_at_1 == 1.0


def test_latency_percentiles_are_na_below_twenty_and_use_nearest_rank(
    tmp_path: Path,
) -> None:
    path = tmp_path / "results.jsonl"
    path.write_text(
        "".join(
            json.dumps(
                {
                    "run_id": "r",
                    "iteration": 1,
                    "scenario_id": "s",
                    "question_id": f"q{i}",
                    "latency_ms": i,
                    "is_correct": True,
                }
            )
            + "\n"
            for i in range(19)
        )
    )
    small = calculate_metrics_from_jsonl(path)
    assert small.latency_sample_size == 19
    assert small.p95_latency_ms is None and small.p99_latency_ms is None

    with path.open("a") as handle:
        handle.write(
            json.dumps(
                {
                    "run_id": "r",
                    "iteration": 1,
                    "scenario_id": "s",
                    "question_id": "q19",
                    "latency_ms": 19,
                    "is_correct": True,
                }
            )
            + "\n"
        )
    large = calculate_metrics_from_jsonl(path)
    assert large.latency_sample_size == 20
    assert large.p95_latency_ms == 18
    assert large.p99_latency_ms == 19


def test_state_persists_hashes_and_question_dedup(tmp_path: Path) -> None:
    state = StateManager(tmp_path / ".state.json")
    state.initialize_state("run", "results.jsonl", config_hash="a", dataset_hash="b")
    state.mark_question_completed("1:s:q")
    state.mark_question_completed("1:s:q")
    assert state.state is not None
    assert state.state.completed_questions == {"1:s:q"}
    loaded = StateManager(tmp_path / ".state.json").load_state()
    assert loaded is not None
    assert loaded.config_hash == "a"
    assert loaded.completed_questions == set()
    assert not (tmp_path / ".state.json.tmp").exists()


def test_mem0_enforces_limit_and_propagates_failures() -> None:
    adapter = Mem0ClientAdapter()
    mock_memory = MagicMock()
    adapter.memory = cast(Any, mock_memory)
    adapter.top_n = 5
    mock_memory.search.return_value = []
    adapter.answer(BenchmarkQuestion(id="q", query="query", ground_truth="answer"))
    assert mock_memory.search.call_args.kwargs["limit"] == 5
    mock_memory.add.side_effect = RuntimeError("provider down")
    with pytest.raises(RuntimeError, match="provider down"):
        adapter.add_memory(MemoryContext(id="c", text="text"))


def test_mem0_applies_provider_native_timeout() -> None:
    client = SimpleNamespace(timeout=None)
    fake_memory = SimpleNamespace(
        llm=SimpleNamespace(client=client),
        embedding_model=SimpleNamespace(client=SimpleNamespace(timeout=None)),
    )
    with patch("mesa_benchmark.clients.mem0_client.Memory") as memory_class:
        memory_class.from_config.return_value = fake_memory
        adapter = Mem0ClientAdapter()
        adapter.initialize({"mem0_config": {}, "timeout_s": 1.25})
    assert fake_memory.llm.client.timeout == 1.25
    assert fake_memory.embedding_model.client.timeout == 1.25


def test_mem0_purges_previous_namespace_on_clear_and_close() -> None:
    adapter = Mem0ClientAdapter()
    memory = MagicMock()
    adapter.memory = cast(Any, memory)
    initial = adapter.current_user_id
    adapter.clear_memory()
    memory.delete_all.assert_called_once_with(user_id=initial)
    active = adapter.current_user_id
    adapter.close()
    assert memory.delete_all.call_args_list[-1].kwargs == {"user_id": active}
    assert adapter.memory is None


def test_mesa_two_pass_ingest_resolves_batch_relation_to_real_node() -> None:
    adapter = MesaClientAdapter()
    adapter.vector = SimpleNamespace(compute_embedding=AsyncMock(return_value=[0.1]))
    adapter.memory_dao = SimpleNamespace(
        insert_memory=AsyncMock(side_effect=["node-a", "node-b"])
    )
    adapter.graph_provider = SimpleNamespace(
        insert_node=AsyncMock(), insert_edge=AsyncMock()
    )
    contexts = [
        MemoryContext(
            id="a",
            text="A knows B",
            metadata={"entity_name": "A", "relations": [{"target": "B"}]},
        ),
        MemoryContext(id="b", text="B exists", metadata={"entity_name": "B"}),
    ]
    try:
        adapter.add_memories(contexts)
        adapter.graph_provider.insert_node.assert_not_awaited()
        adapter.graph_provider.insert_edge.assert_awaited_once_with(
            source_id="node-a",
            target_id="node-b",
            weight=1.0,
            agent_id="benchmark",
        )
    finally:
        adapter.close()


def test_mesa_adapter_owns_and_closes_one_event_loop_worker() -> None:
    policy = asyncio.get_event_loop_policy()
    adapter = MesaClientAdapter()
    assert adapter._worker is None
    assert adapter._run(asyncio.sleep(0, result="ok")) == "ok"
    assert adapter._worker is not None
    worker = adapter._worker
    adapter.close()
    assert not worker.thread.is_alive()
    assert asyncio.get_event_loop_policy() is policy


class _FakeOllamaHandler(BaseHTTPRequestHandler):
    model_tags = [
        "fake-generator:8b",
        "fake-judge:8b",
        "nomic-embed-text:latest",
    ]

    def log_message(self, *_args: Any) -> None:
        return

    def _json(self, payload: dict) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path == "/api/tags":
            self._json(
                {"models": [{"name": name, "model": name} for name in self.model_tags]}
            )
            return
        self.send_error(404)

    def do_POST(self) -> None:
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size) or b"{}")
        properties = (body.get("format") or {}).get("properties", {})
        if "ok" in properties:
            content = '{"ok": true}'
        elif "is_correct" in properties:
            content = '{"is_correct": true, "score": 0.9, "reasoning": "ok"}'
        else:
            content = "Ankara"
        self._json(
            {
                "model": body.get("model"),
                "created_at": "2026-01-01T00:00:00Z",
                "message": {"role": "assistant", "content": content},
                "done": True,
                "prompt_eval_count": 12,
                "eval_count": 2,
            }
        )


@pytest.fixture
def fake_ollama(monkeypatch: pytest.MonkeyPatch) -> Any:
    if os.environ.get("MESA_RUN_SOCKET_TESTS") != "1":
        pytest.skip("local sockets are unavailable in the default sandbox")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("BENCHMARK_OLLAMA_URL", url)
    monkeypatch.setenv("BENCHMARK_GENERATOR_MODEL", "fake-generator:8b")
    monkeypatch.setenv("BENCHMARK_JUDGE_MODEL", "fake-judge:8b")
    try:
        yield url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_fake_ollama_preflight_and_common_generator(
    tmp_path: Path, fake_ollama: str
) -> None:
    config_path = _write_dummy_run(tmp_path)
    config = load_config(config_path)
    config = config.model_copy(
        update={
            "generation": config.generation.model_copy(
                update={"enabled": True, "model": "fake-generator:8b"}
            ),
            "evaluation": config.evaluation.model_copy(
                update={"llm_judge_model": "fake-judge:8b"}
            ),
        }
    )
    result = ollama_preflight(config)
    assert result["json_smoke_ok"] is True
    generator = OllamaAnswerGenerator(
        host=fake_ollama,
        model="fake-generator:8b",
        timeout_s=2,
        temperature=0,
        seed=42,
    )
    generated = generator.generate(
        BenchmarkResponse(
            latency_ms=1,
            retrieved_contexts=[RetrievedContext(id="c", text="Ankara", rank=1)],
        ),
        BenchmarkQuestion(id="q", query="Capital?", ground_truth="Ankara"),
    )
    assert generated.answer == "Ankara"
    assert generated.prompt_tokens == 12


def test_judges_use_ollama_schema_on_arbitrary_port(fake_ollama: str) -> None:
    single = LLMJudgeEvaluator(judge_model="fake-judge:8b", ensemble_size=1)
    assert single._call_litellm("judge") == {
        "is_correct": True,
        "score": 0.9,
        "reasoning": "ok",
    }
    assert multi_call("fake-judge:8b", "judge") == {
        "is_correct": True,
        "score": 0.9,
        "reasoning": "ok",
    }


def test_judge_quorum_failures_are_infrastructure_errors() -> None:
    question = BenchmarkQuestion(id="q", query="q", ground_truth="truth")
    response = BenchmarkResponse(answer_text="answer", latency_ms=1)
    single = LLMJudgeEvaluator(ensemble_size=3, quorum=2)
    with patch.object(single, "_call_litellm", return_value=None):
        with pytest.raises(RuntimeError, match="quorum failed"):
            single.evaluate(response, question)
    multiple = MultiModelJudgeEvaluator(["a", "b"])
    with patch(
        "mesa_benchmark.evaluators.multi_model_judge._call_litellm",
        return_value=None,
    ):
        with pytest.raises(RuntimeError, match="All judge models failed"):
            multiple.evaluate(response, question)


def test_mem0_runner_end_to_end_with_fake_ollama(
    tmp_path: Path, fake_ollama: str
) -> None:
    class FakeMemory:
        def __init__(self) -> None:
            self.items: list[tuple[str, dict]] = []
            self.llm = SimpleNamespace(client=SimpleNamespace(timeout=None))
            self.embedding_model = SimpleNamespace(client=SimpleNamespace(timeout=None))

        def add(self, text: str, **kwargs: Any) -> None:
            self.items.append((text, kwargs["metadata"]))

        def search(self, _query: str, **_kwargs: Any) -> list[dict[str, Any]]:
            return [
                {"memory": text, "metadata": metadata, "score": 1.0}
                for text, metadata in self.items
            ]

        def delete_all(self, *, user_id: str) -> None:
            self.items.clear()

    config = _write_dummy_run(tmp_path)
    config.write_text(
        config.read_text()
        .replace("name: dummy", "name: mem0")
        .replace(
            "mesa_benchmark.clients.dummy_client.DummyClientAdapter",
            "mesa_benchmark.clients.mem0_client.Mem0ClientAdapter",
        )
        .replace("enabled: false", "enabled: true\n  model: fake-generator:8b")
    )
    memory = FakeMemory()
    with patch("mesa_benchmark.clients.mem0_client.Memory") as memory_class:
        memory_class.from_config.return_value = memory
        outcome = BenchmarkRunner(config, results_root=tmp_path / "results").run()
    assert outcome["metrics"]["valid"] is True
    assert outcome["metrics"]["answer_exact_match"] == 1.0


@pytest.mark.skipif(
    os.environ.get("MESA_RUN_REAL_STORAGE_TESTS") != "1",
    reason="requires filesystem capabilities unavailable in the default sandbox",
)
def test_mesa_runner_end_to_end_with_real_storage_and_fake_ollama(
    tmp_path: Path, fake_ollama: str
) -> None:
    config = _write_dummy_run(tmp_path)
    config.write_text(
        config.read_text()
        .replace("name: dummy", "name: mesa")
        .replace(
            "mesa_benchmark.clients.dummy_client.DummyClientAdapter",
            "mesa_benchmark.clients.mesa_client.MesaClientAdapter",
        )
        .replace("enabled: false", "enabled: true\n  model: fake-generator:8b")
    )
    outcome = BenchmarkRunner(config, results_root=tmp_path / "results").run()
    assert outcome["metrics"]["valid"] is True
    assert outcome["metrics"]["answer_exact_match"] == 1.0


def test_runtime_uses_one_canonical_ollama_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BENCHMARK_OLLAMA_URL", raising=False)
    monkeypatch.setenv("MESA_OLLAMA_URL", "http://old.invalid:11434")
    monkeypatch.setenv("OLLAMA_HOST", "http://old.invalid:11434")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://old.invalid:11434/v1")
    dataset = tmp_path / "dataset.json"
    dataset.write_text("[]")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"""
suite_name: test
iterations: 1
seed: 7
dataset:
  name: test
  path: {dataset}
client:
  name: dummy
  adapter_class: mesa_benchmark.clients.dummy_client.DummyClientAdapter
evaluation:
  enable_agreement: false
runtime:
  ollama_url: http://remote:11434/v1
""")
    config = load_config(config_path)
    apply_runtime_environment(config)
    import os

    assert os.environ["MESA_OLLAMA_URL"] == "http://remote:11434"
    assert os.environ["OLLAMA_HOST"] == "http://remote:11434"
    assert os.environ["OPENAI_BASE_URL"] == "http://remote:11434/v1"


def test_config_environment_is_applied_before_validation_and_models_are_frozen(
    tmp_path: Path,
) -> None:
    config_path = _write_dummy_run(tmp_path)
    config = load_config(
        config_path,
        environ={
            "BENCHMARK_GENERATOR_MODEL": "generator:8b",
            "BENCHMARK_JUDGE_MODEL": "judge:8b",
            "BENCHMARK_OLLAMA_URL": "http://remote:11434",
        },
    )
    assert config.generation.model == "generator:8b"
    assert config.evaluation.llm_judge_model == "judge:8b"
    assert config.runtime.ollama_url == "http://remote:11434"
    with pytest.raises(Exception):
        config.seed = 99  # type: ignore[misc]


def test_resume_rebuilds_question_dedup_from_jsonl_not_state_list(
    tmp_path: Path,
) -> None:
    config = _write_dummy_run(tmp_path)
    results_root = tmp_path / "results"
    runner = BenchmarkRunner(config, results_root=results_root)
    runner.setup()
    assert runner.state_manager and runner.state_manager.state and runner.client
    state = runner.state_manager.state
    key = "1:s1:q1"
    Path(state.results_file).write_text(
        json.dumps(
            {
                "run_id": runner.run_id,
                "iteration": 1,
                "scenario_id": "s1",
                "question_id": "q1",
            }
        )
        + "\n"
    )
    state.completed_questions = {"legacy:wrong:key"}
    runner.state_manager.save_state()
    runner.client.close()

    resumed = BenchmarkRunner(config, results_root=results_root)
    resumed.setup()
    assert key in resumed.completed_questions
    assert "legacy:wrong:key" not in resumed.completed_questions
    assert resumed.state_manager and resumed.state_manager.state
    assert resumed.state_manager.state.completed_questions == set()
    assert resumed.client is not None
    resumed.client.close()


def test_comprehensive_multihop_metadata_has_resolvable_entity_nodes() -> None:
    path = Path("mesa-benchmark/mesa_benchmark/datasets/comprehensive_200_dataset.json")
    scenarios = json.loads(path.read_text())
    multi_hop = [item for item in scenarios if item["id"].startswith("multi_hop")]
    assert len(multi_hop) == 60
    for scenario in multi_hop:
        names = {
            context["metadata"].get("entity_name") for context in scenario["contexts"]
        }
        first_target = scenario["contexts"][0]["metadata"]["relations"][0]["target"]
        assert first_target in names


def test_mesa_evals_and_comparison_benchmark_have_no_source_dependency() -> None:
    benchmark_sources = Path("mesa-benchmark/mesa_benchmark")
    for source in benchmark_sources.rglob("*.py"):
        assert "mesa_evals" not in source.read_text(encoding="utf-8")


def _write_dummy_run(tmp_path: Path) -> Path:
    dataset = tmp_path / "mini.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "id": "s1",
                    "name": "mini",
                    "contexts": [{"id": "c1", "text": "Ankara is the capital."}],
                    "questions": [
                        {
                            "id": "q1",
                            "query": "What is the capital?",
                            "ground_truth": "Ankara",
                            "expected_context_ids": ["c1"],
                            "evaluation_strategy": "exact_match",
                        }
                    ],
                }
            ]
        )
    )
    config = tmp_path / "mini.yaml"
    config.write_text(f"""
suite_name: offline-e2e
iterations: 1
seed: 11
dataset:
  name: mini_offline
  path: {dataset}
client:
  name: dummy
  adapter_class: mesa_benchmark.clients.dummy_client.DummyClientAdapter
  timeout_ms: 2000
evaluation:
  metrics: [hit_at_k, mrr, latency]
  enable_agreement: false
generation:
  enabled: false
runtime:
  top_k: 5
  require_independent_judge: true
""")
    return config


def test_config_and_dataset_check_validate_hashes_and_structure(tmp_path: Path) -> None:
    config = _write_dummy_run(tmp_path)
    config_summary = validate_config(config)
    dataset_summary = validate_config_and_dataset(config)
    assert config_summary["top_k"] == 5
    assert dataset_summary["questions"] == 1
    assert dataset_summary["retrieval_metrics_supported"] is True
    assert dataset_summary["dataset_sha256"] == file_sha256(tmp_path / "mini.json")


def test_dataset_check_rejects_unresolved_graph_relation(tmp_path: Path) -> None:
    config = _write_dummy_run(tmp_path)
    dataset_path = tmp_path / "mini.json"
    data = json.loads(dataset_path.read_text())
    data[0]["contexts"][0]["metadata"] = {
        "entity_name": "A",
        "relations": [{"target": "missing"}],
    }
    dataset_path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="unresolved graph relations"):
        validate_config_and_dataset(config)


def test_resume_rejects_effective_config_hash_change(tmp_path: Path) -> None:
    config = _write_dummy_run(tmp_path)
    results_root = tmp_path / "results"
    first = BenchmarkRunner(config, results_root=results_root)
    first.setup()
    assert first.client is not None
    first.client.close()
    config.write_text(config.read_text().replace("offline-e2e", "offline-e2e-changed"))
    with pytest.raises(RuntimeError, match="config hash changed"):
        BenchmarkRunner(config, results_root=results_root).setup()


def test_reproduce_comparison_pairs_identical_seed_and_question_keys(
    tmp_path: Path,
) -> None:
    script = Path("scripts/reproduce_benchmark.py")
    spec = importlib.util.spec_from_file_location("reproduce_benchmark_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    left_file = tmp_path / "left.jsonl"
    right_file = tmp_path / "right.jsonl"
    rows = [
        {"iteration": 1, "scenario_id": "s", "question_id": "q1"},
        {"iteration": 1, "scenario_id": "s", "question_id": "q2"},
    ]
    left_file.write_text(
        "\n".join(json.dumps({**row, "is_correct": True}) for row in rows) + "\n"
    )
    right_file.write_text(
        "\n".join(json.dumps({**row, "is_correct": False}) for row in rows) + "\n"
    )

    def system(path: Path, accuracy: float) -> dict:
        return {
            "runs": [
                {
                    "seed": 42,
                    "status": "success",
                    "results_file": str(path),
                    "metrics": {metric: accuracy for metric in module.SUMMARY_METRICS},
                }
            ]
        }

    comparison = module._compare(system(left_file, 1.0), system(right_file, 0.0))
    paired = comparison["same_question_accuracy"]["paired_t_test"]
    assert paired["n"] == 2
    assert paired["mean_difference"] == 1.0


def test_runner_offline_end_to_end_writes_valid_manifest_and_result(
    tmp_path: Path,
) -> None:
    config = _write_dummy_run(tmp_path)
    outcome = BenchmarkRunner(config, results_root=tmp_path / "results").run()
    assert outcome["metrics"]["valid"] is True
    assert outcome["metrics"]["retrieval_evaluable_questions"] == 1
    assert outcome["metrics"]["quality_tier"] == "provisional/self-judged"
    result_rows = [
        json.loads(line)
        for line in Path(outcome["results_file"]).read_text().splitlines()
    ]
    assert len(result_rows) == 1
    assert result_rows[0]["answer_exact_match"] is None
    manifests = list(Path(outcome["results_file"]).parent.glob("manifest_*.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["top_k"] == 5


def test_runner_fails_before_client_setup_when_judge_contract_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BENCHMARK_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("BENCHMARK_JUDGE_MODELS", raising=False)
    config = _write_dummy_run(tmp_path)
    config.write_text(
        config.read_text().replace("enable_agreement: false", "enable_agreement: true")
    )
    with patch(
        "mesa_benchmark.clients.dummy_client.DummyClientAdapter.initialize"
    ) as initialize:
        with pytest.raises(ValueError, match="enable_agreement requires"):
            BenchmarkRunner(config, results_root=tmp_path / "results").setup()
    initialize.assert_not_called()


def test_seed_is_applied_before_client_setup(tmp_path: Path) -> None:
    config = _write_dummy_run(tmp_path)
    observed: list[float] = []
    with patch(
        "mesa_benchmark.clients.dummy_client.DummyClientAdapter.initialize",
        side_effect=lambda _params: observed.append(random.random()),
    ):
        runner = BenchmarkRunner(config, results_root=tmp_path / "results")
        runner.setup()
        assert runner.client is not None
        runner.client.close()
    assert observed == [random.Random(11).random()]
    assert runner.results_dir is not None
    assert runner.results_dir.name.endswith("seed11")


def test_evidence_tier_requires_a_different_judge_that_actually_ran(
    tmp_path: Path,
) -> None:
    config_path = _write_dummy_run(tmp_path)
    runner = BenchmarkRunner(config_path)
    config = load_config(config_path)
    runner.config = config.model_copy(
        update={
            "generation": config.generation.model_copy(
                update={"model": "generator:8b"}
            ),
            "evaluation": config.evaluation.model_copy(
                update={"llm_judge_model": "judge:8b"}
            ),
        }
    )
    assert runner._quality_tier(0) == "provisional/self-judged"
    runner.judge_evaluations = 1
    assert runner._quality_tier(0) == "publishable"
    runner.config = runner.config.model_copy(
        update={
            "evaluation": runner.config.evaluation.model_copy(
                update={"llm_judge_model": "openai/generator:8b"}
            )
        }
    )
    assert runner._quality_tier(0) == "provisional/self-judged"
    assert runner._quality_tier(1) == "invalid"


def test_runner_marks_query_provider_failure_invalid(tmp_path: Path) -> None:
    config = _write_dummy_run(tmp_path)
    with patch(
        "mesa_benchmark.clients.dummy_client.DummyClientAdapter.answer",
        side_effect=RuntimeError("provider unavailable"),
    ):
        with pytest.raises(BenchmarkRunInvalid, match="run invalid"):
            BenchmarkRunner(config, results_root=tmp_path / "results").run()
    state_path = next((tmp_path / "results").rglob(".state.json"))
    state = json.loads(state_path.read_text())
    assert state["status"] == "failed"
    assert state["infrastructure_errors"] == 1


def test_runner_marks_client_close_failure_invalid(tmp_path: Path) -> None:
    config = _write_dummy_run(tmp_path)
    with patch(
        "mesa_benchmark.clients.dummy_client.DummyClientAdapter.close",
        side_effect=RuntimeError("close failed"),
    ):
        with pytest.raises(BenchmarkRunInvalid, match="run invalid"):
            BenchmarkRunner(config, results_root=tmp_path / "results").run()
    state_path = next((tmp_path / "results").rglob(".state.json"))
    state = json.loads(state_path.read_text())
    assert state["infrastructure_errors"] == 1
