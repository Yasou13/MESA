import hashlib
import importlib
import json
import logging
import os
import random
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from ..clients.base import AbstractBenchmarkClient, BenchmarkResponse
from ..datasets.loader import DatasetManager
from ..evaluators.agreement import compute_agreement
from ..evaluators.base import BaseEvaluator, EvaluationResult
from ..evaluators.exact_match import ExactMatchEvaluator
from ..evaluators.qa_metrics import exact_match, token_f1
from ..metrics.calculator import calculate_metrics_from_jsonl
from ..reports.reporter import MarkdownReporter
from .config import BenchmarkConfig, apply_runtime_environment, load_config
from .generation import OllamaAnswerGenerator
from .preflight import file_sha256
from .state_manager import StateManager

logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


class MemoryPurgeError(RuntimeError):
    """Memory isolation could not be proven."""


class BenchmarkTimeoutError(TimeoutError):
    """A benchmark operation exceeded its hard deadline."""


class BenchmarkRunInvalid(RuntimeError):
    """The run produced infrastructure failures and is not scoreable."""


class BenchmarkRunner:
    def __init__(
        self,
        config_path: str | Path,
        *,
        seed: Optional[int] = None,
        results_root: str | Path = "results",
    ) -> None:
        self.config_path = Path(config_path)
        self.seed_override = seed
        self.results_root = Path(results_root)
        self.config: Optional[BenchmarkConfig] = None
        self.run_id = str(uuid.uuid4())
        self.results_dir: Optional[Path] = None
        self.state_manager: Optional[StateManager] = None
        self.dataset_manager: Optional[DatasetManager] = None
        self.client: Optional[AbstractBenchmarkClient] = None
        self.generator: Optional[OllamaAnswerGenerator] = None
        self.evaluators: Dict[str, BaseEvaluator] = {}
        self.completed_questions: set[str] = set()
        self.judge_evaluations = 0

    def _register_evaluators(self) -> None:
        assert self.config is not None
        evaluation = self.config.evaluation
        self.evaluators["exact_match"] = ExactMatchEvaluator()

        from ..evaluators.regex import RegexEvaluator

        self.evaluators["regex"] = RegexEvaluator()

        if evaluation.llm_judge_model:
            from ..evaluators.llm_judge import LLMJudgeEvaluator

            self.evaluators["llm_judge"] = LLMJudgeEvaluator(
                judge_model=evaluation.llm_judge_model,
                ensemble_size=evaluation.judge_ensemble_size,
                quorum=evaluation.judge_quorum,
                timeout_s=evaluation.judge_timeout_s,
                seed=self.config.seed,
            )

        distinct_models = list(
            dict.fromkeys(
                model.removeprefix("openai/") for model in evaluation.multi_judge_models
            )
        )
        if len(distinct_models) >= 2:
            from ..evaluators.multi_model_judge import MultiModelJudgeEvaluator

            self.evaluators["multi_model_judge"] = MultiModelJudgeEvaluator(
                judge_models=distinct_models,
                timeout_s=evaluation.judge_timeout_s,
                max_concurrency=evaluation.judge_max_concurrency,
            )
        elif evaluation.multi_judge_models:
            logger.warning(
                "Only one distinct judge model is configured; run is self-judged/provisional."
            )

    def _get_evaluator(self, strategy: str) -> BaseEvaluator:
        if strategy in self.evaluators:
            return self.evaluators[strategy]
        raise ValueError(
            f"Unknown or unavailable evaluation strategy {strategy!r}; "
            f"available={sorted(self.evaluators)}"
        )

    @staticmethod
    def _evaluator_family(strategy: str) -> str:
        if strategy in {"llm_judge", "multi_model_judge"}:
            return "semantic_judge"
        if strategy in {"exact_match", "regex"}:
            return "deterministic"
        return "other"

    def _validate_execution_contract(self) -> None:
        assert self.config is not None and self.dataset_manager is not None
        required = {
            question.evaluation_strategy
            for scenario in self.dataset_manager.scenarios
            for question in scenario.questions
        }
        missing = sorted(required.difference(self.evaluators))
        if missing:
            raise ValueError(
                f"dataset requires unavailable evaluators: {missing}; "
                "configure the required judge model(s)"
            )
        if self.config.evaluation.enable_agreement and not {
            "llm_judge",
            "multi_model_judge",
        }.intersection(self.evaluators):
            raise ValueError(
                "evaluation.enable_agreement requires at least one configured judge"
            )

    def _load_client(self) -> None:
        assert self.config is not None
        module_path, class_name = self.config.client.adapter_class.rsplit(".", 1)
        module = importlib.import_module(module_path)
        adapter_class = getattr(module, class_name)
        client = adapter_class()
        if not isinstance(client, AbstractBenchmarkClient):
            raise TypeError(f"{class_name} must inherit from AbstractBenchmarkClient")
        parameters = dict(self.config.client.parameters)
        parameters["top_n"] = self.config.runtime.top_k
        parameters["timeout_s"] = self.config.client.timeout_ms / 1000.0
        try:
            client.initialize(parameters)
        except Exception:
            try:
                client.close()
            except Exception:
                logger.warning(
                    "Partially initialized client cleanup failed", exc_info=True
                )
            raise
        self.client = client

    def _load_generator(self) -> None:
        assert self.config is not None
        if not self.config.generation.enabled:
            return
        model = self.config.generation.model or os.environ.get(
            "BENCHMARK_GENERATOR_MODEL"
        )
        host = os.environ.get("BENCHMARK_OLLAMA_URL", "")
        if not model:
            raise ValueError("generation.enabled requires a generator model")
        self.generator = OllamaAnswerGenerator(
            host=host,
            model=model,
            timeout_s=self.config.generation.timeout_s,
            temperature=self.config.generation.temperature,
            seed=self.config.seed,
        )

    def _question_key(self, iteration: int, scenario_id: str, question_id: str) -> str:
        return f"{iteration}:{scenario_id}:{question_id}"

    def _append_result(self, result_dict: dict, question_key: str) -> None:
        if not self.state_manager or not self.state_manager.state:
            raise RuntimeError("state is not initialized")
        results_file = Path(self.state_manager.state.results_file)
        results_file.parent.mkdir(parents=True, exist_ok=True)
        with open(results_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(result_dict, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.completed_questions.add(question_key)

    def _call_with_backoff(
        self, func: Any, *args: Any, max_retries: int = 3, **kwargs: Any
    ) -> Any:
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except TimeoutError as exc:
                raise BenchmarkTimeoutError(
                    f"{getattr(func, '__name__', 'operation')} exceeded its provider timeout"
                ) from exc
            except Exception:
                if attempt >= max_retries - 1:
                    raise
                wait_time = 2**attempt
                logger.warning(
                    "Attempt %d/%d failed; retrying in %ds",
                    attempt + 1,
                    max_retries,
                    wait_time,
                    exc_info=True,
                )
                time.sleep(wait_time)
        raise RuntimeError("unreachable retry state")

    def setup(self) -> None:
        self.config = load_config(self.config_path)
        if self.seed_override is not None:
            self.config = self.config.model_copy(update={"seed": self.seed_override})
        apply_runtime_environment(self.config)

        random.seed(self.config.seed)
        try:
            import numpy as np

            np.random.seed(self.config.seed)
        except ImportError:
            pass

        client_name = self.config.client.name
        dataset_name = self.config.dataset.name
        dataset_ver = self.config.dataset.version
        seed = self.config.seed
        self.results_dir = (
            self.results_root / client_name / f"{dataset_name}_{dataset_ver}_seed{seed}"
        )
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.state_manager = StateManager(self.results_dir / ".state.json")

        self.dataset_manager = DatasetManager(
            self.config.dataset.path, self.config.dataset.noise_ratio
        )
        self.dataset_manager.load()

        config_file_hash = file_sha256(self.config_path)
        config_hash = hashlib.sha256(
            self.config.model_dump_json(exclude_none=False).encode("utf-8")
        ).hexdigest()
        dataset_hash = file_sha256(self.config.dataset.path)
        existing = self.state_manager.load_state()
        if existing and existing.status == "running":
            if not existing.config_hash or not existing.dataset_hash:
                raise RuntimeError(
                    "refusing resume: legacy state lacks config/dataset hashes"
                )
            if existing.config_hash and existing.config_hash != config_hash:
                raise RuntimeError("refusing resume: config hash changed")
            if existing.dataset_hash and existing.dataset_hash != dataset_hash:
                raise RuntimeError("refusing resume: dataset hash changed")
            self.run_id = existing.run_id
            self.completed_questions = self._completed_keys_from_results(
                existing.results_file, existing.run_id
            )
            # A legacy state may predate JSONL durability. Preserve its keys only
            # for that one migration; all new checkpoints keep this field empty.
            if not self.completed_questions:
                self.completed_questions = set(existing.completed_questions)
            existing.completed_questions.clear()
        else:
            self.state_manager.initialize_state(
                self.run_id,
                str(self.results_dir / f"results_{self.run_id}.jsonl"),
                config_hash=config_hash,
                dataset_hash=dataset_hash,
            )

        self._register_evaluators()
        self._validate_execution_contract()
        self._load_generator()
        self._load_client()
        self._write_manifest(config_hash, dataset_hash, config_file_hash)

    def _completed_keys_from_results(self, results_file: str, run_id: str) -> set[str]:
        """Rebuild resume deduplication from durable append-only JSONL output."""
        path = Path(results_file)
        if not path.exists():
            return set()
        completed: set[str] = set()
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("run_id") != run_id:
                    continue
                try:
                    completed.add(
                        self._question_key(
                            int(row["iteration"]),
                            str(row["scenario_id"]),
                            str(row["question_id"]),
                        )
                    )
                except KeyError:
                    logger.warning("Ignoring malformed resume record in %s", path)
        return completed

    def _write_manifest(
        self, config_hash: str, dataset_hash: str, config_file_hash: str
    ) -> None:
        assert self.config is not None and self.results_dir is not None
        manifest = {
            "schema_version": 1,
            "run_id": self.run_id,
            "suite": self.config.suite_name,
            "seed": self.config.seed,
            "top_k": self.config.runtime.top_k,
            "config_sha256": config_hash,
            "config_file_sha256": config_file_hash,
            "dataset_sha256": dataset_hash,
            "generator_model": self.config.generation.model,
            "judge_model": self.config.evaluation.llm_judge_model,
            "multi_judge_models": self.config.evaluation.multi_judge_models,
            "embedding_model": (
                "sentence-transformers/all-MiniLM-L6-v2"
                if self.config.client.name.lower().startswith("mesa")
                else os.environ.get("BENCHMARK_EMBEDDING_MODEL")
            ),
            "evidence_tier": self._quality_tier(0),
            "dataset_designation": (
                "internal-regression-only"
                if self.config.dataset.name.startswith(("mini", "comprehensive"))
                else "external-benchmark"
            ),
        }
        path = self.results_dir / f"manifest_{self.run_id}.json"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)

    def _dual_evaluate(
        self, response: BenchmarkResponse, question: Any
    ) -> tuple[EvaluationResult, Optional[EvaluationResult]]:
        primary_evaluator = self._get_evaluator(question.evaluation_strategy)
        primary = primary_evaluator.evaluate(response, question)
        if "JudgeEvaluator" in type(primary_evaluator).__name__:
            self.judge_evaluations += 1
        if not self.config or not self.config.evaluation.enable_agreement:
            return primary, None
        if question.evaluation_strategy in ("llm_judge", "multi_model_judge"):
            secondary_evaluator = self.evaluators["exact_match"]
        elif "multi_model_judge" in self.evaluators:
            secondary_evaluator = self.evaluators["multi_model_judge"]
        elif "llm_judge" in self.evaluators:
            secondary_evaluator = self.evaluators["llm_judge"]
        else:
            raise RuntimeError("agreement is enabled but no judge is configured")
        secondary = secondary_evaluator.evaluate(response, question)
        if "JudgeEvaluator" in type(secondary_evaluator).__name__:
            self.judge_evaluations += 1
        return primary, secondary

    def _apply_generation(
        self, response: BenchmarkResponse, question: Any
    ) -> BenchmarkResponse:
        if self.generator is None:
            return response
        generated = self.generator.generate(response, question)
        return response.model_copy(
            update={
                "answer_text": generated.answer,
                "generation_latency_ms": generated.latency_ms,
                "token_usage": {
                    "prompt": generated.prompt_tokens,
                    "completion": generated.completion_tokens,
                },
            }
        )

    def _quality_tier(self, infrastructure_errors: int) -> str:
        assert self.config is not None
        if infrastructure_errors:
            return "invalid"
        generator = (self.config.generation.model or "").removeprefix("openai/")
        judges = {
            item.removeprefix("openai/")
            for item in [
                self.config.evaluation.llm_judge_model or "",
                *self.config.evaluation.multi_judge_models,
            ]
            if item
        }
        if (
            not generator
            or not any(item != generator for item in judges)
            or self.judge_evaluations == 0
        ):
            return "provisional/self-judged"
        return "publishable"

    def _agreement_from_results(self, result_file: str) -> dict[str, Any]:
        paired: dict[str, tuple[float, float]] = {}
        with open(result_file, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if "secondary_score" not in row:
                    continue
                key = self._question_key(
                    row["iteration"], row["scenario_id"], row["question_id"]
                )
                if row["evaluation_strategy"] in ("llm_judge", "multi_model_judge"):
                    paired[key] = (row["secondary_score"], row["score"])
                else:
                    paired[key] = (row["score"], row["secondary_score"])
        if not paired:
            return {}
        keyword, judge = zip(*paired.values())
        return compute_agreement(list(keyword), list(judge))

    def run(self) -> dict[str, Any]:
        if not self.config or not self.dataset_manager or not self.client:
            self.setup()
        assert self.config is not None
        assert self.dataset_manager is not None
        assert self.client is not None
        assert self.state_manager is not None
        assert self.state_manager.state is not None

        state = self.state_manager.state
        infrastructure_errors = state.infrastructure_errors
        total_scenarios = len(self.dataset_manager)
        if "MESA_MAX_SCENARIOS" in os.environ:
            total_scenarios = min(
                total_scenarios, int(os.environ["MESA_MAX_SCENARIOS"])
            )

        try:
            start_iteration = state.current_iteration
            for iteration in range(start_iteration, self.config.iterations + 1):
                try:
                    self.client.clear_memory()
                except Exception as exc:
                    raise MemoryPurgeError(
                        f"clear_memory failed; isolation is unproven: {exc}"
                    ) from exc

                start_scenario = (
                    state.current_scenario_idx if iteration == start_iteration else 0
                )
                for rebuild_index in range(start_scenario):
                    scenario = self.dataset_manager.get_scenario(rebuild_index)
                    self._call_with_backoff(self.client.add_memories, scenario.contexts)

                for scenario_index in range(start_scenario, total_scenarios):
                    scenario = self.dataset_manager.get_scenario(scenario_index)
                    self._call_with_backoff(self.client.add_memories, scenario.contexts)
                    for question in scenario.questions:
                        key = self._question_key(iteration, scenario.id, question.id)
                        if key in self.completed_questions:
                            continue
                        try:
                            response = self._call_with_backoff(
                                self.client.answer, question
                            )
                            response = response.enforce_top_k(self.config.runtime.top_k)
                            response = self._apply_generation(response, question)
                            primary, secondary = self._dual_evaluate(response, question)
                            expected = question.expected_context_ids
                            has_hit = any(
                                item in response.retrieved_context_ids
                                for item in expected
                            )
                            if primary.is_correct:
                                failure = "SUCCESS"
                            elif expected and not has_hit:
                                failure = "RETRIEVAL_MISS"
                            elif (
                                len(response.retrieved_context_ids) > len(expected) + 3
                            ):
                                failure = "CONTEXT_NOISE"
                            else:
                                failure = "LLM_REASONING_ERROR"
                            record: dict[str, Any] = {
                                "schema_version": 2,
                                "run_id": self.run_id,
                                "iteration": iteration,
                                "scenario_id": scenario.id,
                                "question_id": question.id,
                                "score": primary.score,
                                "is_correct": primary.is_correct,
                                "latency_ms": response.retrieval_latency_ms,
                                "retrieval_latency_ms": response.retrieval_latency_ms,
                                "generation_latency_ms": response.generation_latency_ms,
                                "ground_truth": question.ground_truth,
                                "actual_answer": response.answer_text,
                                "answer_exact_match": (
                                    exact_match(
                                        response.answer_text, question.ground_truth
                                    )
                                    if self.generator
                                    else None
                                ),
                                "answer_token_f1": (
                                    token_f1(
                                        response.answer_text, question.ground_truth
                                    )
                                    if self.generator
                                    else None
                                ),
                                "expected_context_ids": expected,
                                "retrieved_context_ids": response.retrieved_context_ids,
                                "prompt_tokens": response.token_usage.get("prompt", 0),
                                "completion_tokens": response.token_usage.get(
                                    "completion", 0
                                ),
                                "evaluation_strategy": question.evaluation_strategy,
                                "primary_evaluator_type": primary.metadata.get(
                                    "evaluator_type", type(primary).__name__
                                ),
                                "evaluator_family": self._evaluator_family(
                                    question.evaluation_strategy
                                ),
                                "failure_attribution": failure,
                                "latency_breakdown_ms": response.metadata.get(
                                    "latency_breakdown_ms", {}
                                ),
                                "diagnostics": response.metadata.get("diagnostics", {}),
                                "infrastructure_error": False,
                            }
                            if secondary is not None:
                                record.update(
                                    secondary_score=secondary.score,
                                    secondary_is_correct=secondary.is_correct,
                                    secondary_evaluator=secondary.metadata.get(
                                        "evaluator_type", "unknown"
                                    ),
                                )
                            self._append_result(record, key)
                        except Exception as exc:
                            infrastructure_errors += 1
                            state.infrastructure_errors = infrastructure_errors
                            self._append_result(
                                {
                                    "schema_version": 2,
                                    "run_id": self.run_id,
                                    "iteration": iteration,
                                    "scenario_id": scenario.id,
                                    "question_id": question.id,
                                    "score": 0.0,
                                    "is_correct": False,
                                    "latency_ms": None,
                                    "retrieval_latency_ms": None,
                                    "generation_latency_ms": None,
                                    "ground_truth": question.ground_truth,
                                    "actual_answer": "",
                                    "expected_context_ids": question.expected_context_ids,
                                    "retrieved_context_ids": [],
                                    "prompt_tokens": 0,
                                    "completion_tokens": 0,
                                    "evaluation_strategy": question.evaluation_strategy,
                                    "primary_evaluator_type": "unavailable",
                                    "evaluator_family": self._evaluator_family(
                                        question.evaluation_strategy
                                    ),
                                    "failure_attribution": "TIMEOUT_OR_ERROR",
                                    "diagnostics": {"error": str(exc)},
                                    "infrastructure_error": True,
                                },
                                key,
                            )
                    self.state_manager.update_progress(iteration, scenario_index + 1)

            try:
                self.client.close()
                self.client = None
            except Exception:
                infrastructure_errors += 1
                state.infrastructure_errors = infrastructure_errors
                logger.error("Client close failed", exc_info=True)

            metrics = calculate_metrics_from_jsonl(state.results_file)
            metrics_dict = metrics.model_dump()
            metrics_dict["agreement"] = self._agreement_from_results(state.results_file)
            metrics_dict["valid"] = infrastructure_errors == 0
            metrics_dict["infrastructure_errors"] = infrastructure_errors
            metrics_dict["quality_tier"] = self._quality_tier(infrastructure_errors)
            reporter = MarkdownReporter(
                self.run_id, self.config, output_dir=str(self.results_dir)
            )
            report_path = reporter.generate_report_from_dict(metrics_dict)
            if infrastructure_errors:
                self.state_manager.mark_failed(
                    f"{infrastructure_errors} infrastructure error(s); run invalid"
                )
                raise BenchmarkRunInvalid(
                    f"run invalid: {infrastructure_errors} infrastructure error(s)"
                )
            self.state_manager.mark_completed()
            return {
                "run_id": self.run_id,
                "results_file": state.results_file,
                "report_file": report_path,
                "metrics": metrics_dict,
            }
        except MemoryPurgeError as exc:
            self.state_manager.mark_failed(str(exc))
            raise
        except BenchmarkRunInvalid:
            raise
        except Exception as exc:
            self.state_manager.mark_failed(str(exc))
            raise
        finally:
            if self.client is not None:
                try:
                    self.client.close()
                except Exception:
                    logger.warning("Client close failed", exc_info=True)
