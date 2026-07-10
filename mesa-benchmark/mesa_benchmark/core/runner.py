import importlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..clients.base import AbstractBenchmarkClient
from ..datasets.loader import DatasetManager
from ..evaluators.agreement import compute_agreement
from ..evaluators.base import BaseEvaluator, EvaluationResult
from ..evaluators.exact_match import ExactMatchEvaluator
from ..metrics.calculator import calculate_metrics_from_jsonl
from ..reports.reporter import MarkdownReporter
from .config import BenchmarkConfig, load_config
from .state_manager import StateManager

logger = logging.getLogger(__name__)

# Load .env if python-dotenv is available
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


class MemoryPurgeError(Exception):
    """Critical error: memory could not be cleared. Benchmark isolation is broken."""

    pass


class BenchmarkRunner:
    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self.config: Optional[BenchmarkConfig] = None
        self.state_manager = StateManager(state_file="state.json")
        self.dataset_manager: Optional[DatasetManager] = None
        self.client: Optional[AbstractBenchmarkClient] = None
        self.evaluators: Dict[str, BaseEvaluator] = {}
        self.run_id = str(uuid.uuid4())

    def _register_evaluators(self) -> None:
        """Registers evaluators based on configuration."""
        self.evaluators["exact_match"] = ExactMatchEvaluator()

        # Single-model LLM judge
        try:
            from ..evaluators.llm_judge import LLMJudgeEvaluator

            judge_model = "gpt-4o-mini"
            if self.config and self.config.evaluation.llm_judge_model:
                judge_model = self.config.evaluation.llm_judge_model
            self.evaluators["llm_judge"] = LLMJudgeEvaluator(judge_model=judge_model)
        except ImportError:
            logger.warning("LLMJudgeEvaluator not available (openai not installed).")

        # Multi-model judge (for independent evaluation / self-grading bias mitigation)
        if self.config and self.config.evaluation.multi_judge_models:
            try:
                from ..evaluators.multi_model_judge import MultiModelJudgeEvaluator

                self.evaluators["multi_model_judge"] = MultiModelJudgeEvaluator(
                    judge_models=self.config.evaluation.multi_judge_models,
                )
                logger.info(
                    "MultiModelJudgeEvaluator registered with models: %s",
                    self.config.evaluation.multi_judge_models,
                )
            except ImportError:
                logger.warning(
                    "MultiModelJudgeEvaluator not available (litellm not installed)."
                )

    def _get_evaluator(self, strategy: str) -> BaseEvaluator:
        """Returns the evaluator for the given strategy, falling back to exact_match."""
        if strategy in self.evaluators:
            return self.evaluators[strategy]
        logger.warning(
            f"Unknown evaluation strategy '{strategy}', falling back to exact_match."
        )
        return self.evaluators["exact_match"]

    def _load_client(self) -> None:
        """Dynamically loads and initializes the client adapter."""
        if not self.config:
            raise ValueError("Config not loaded")

        module_path, class_name = self.config.client.adapter_class.rsplit(".", 1)
        logger.info(f"Loading client adapter: {class_name} from {module_path}")

        try:
            module = importlib.import_module(module_path)
            adapter_class = getattr(module, class_name)
            self.client = adapter_class()
            if not isinstance(self.client, AbstractBenchmarkClient):
                raise TypeError(
                    f"{class_name} must inherit from AbstractBenchmarkClient"
                )

            self.client.initialize(self.config.client.parameters)
            logger.info("Client adapter initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to load client adapter: {e}")
            raise

    def _append_result(self, result_dict: dict) -> None:
        """Appends a result JSON object to the JSONL results file (Atomic Transaction)."""
        if not self.state_manager.state:
            return
        results_file = Path(self.state_manager.state.results_file)
        with open(results_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(result_dict) + "\n")

    def _call_with_backoff(
        self, func: Any, *args: Any, max_retries: int = 3, **kwargs: Any
    ) -> Any:
        """Calls a function with exponential backoff on failure."""
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                wait_time = 2**attempt
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s..."
                    )
                    time.sleep(wait_time)
                else:
                    raise

    def setup(self) -> None:
        """Loads configuration, dataset, and initializes state and client."""
        logger.info(f"Loading configuration from {self.config_path}")
        self.config = load_config(self.config_path)

        logger.info(f"Loading dataset from {self.config.dataset.path}")
        self.dataset_manager = DatasetManager(self.config.dataset.path)
        self.dataset_manager.load()
        logger.info(f"Successfully loaded {len(self.dataset_manager)} scenarios.")

        self._load_client()
        self._register_evaluators()

        # Check if we are resuming
        existing_state = self.state_manager.load_state()
        if existing_state and existing_state.status == "running":
            logger.info(f"Resuming previous run: {existing_state.run_id}")
            self.run_id = existing_state.run_id
        else:
            logger.info(f"Starting new run: {self.run_id}")
            self.state_manager.initialize_state(
                run_id=self.run_id, results_file=f"results_{self.run_id}.jsonl"
            )

    def _dual_evaluate(
        self,
        response: Any,
        question: Any,
    ) -> tuple[EvaluationResult, Optional[EvaluationResult]]:
        """
        Evaluates a response using both the primary evaluator and the secondary
        evaluator (if agreement tracking is enabled).

        Returns (primary_result, secondary_result_or_None).
        """
        primary_eval = self._get_evaluator(question.evaluation_strategy)
        primary_result = primary_eval.evaluate(response, question)

        secondary_result = None

        if self.config and self.config.evaluation.enable_agreement:
            if question.evaluation_strategy in ("llm_judge", "multi_model_judge"):
                # Primary is already judge -> secondary should be exact_match for comparison
                secondary_eval = self.evaluators["exact_match"]
            else:
                # Primary is exact_match -> secondary should be best available judge
                if "multi_model_judge" in self.evaluators:
                    secondary_eval = self.evaluators["multi_model_judge"]
                elif "llm_judge" in self.evaluators:
                    secondary_eval = self.evaluators["llm_judge"]
                else:
                    return primary_result, None

            try:
                secondary_result = secondary_eval.evaluate(response, question)
            except Exception as e:
                logger.warning("Secondary evaluation failed: %s", e)

        return primary_result, secondary_result

    def run(self) -> None:
        """Main execution loop."""
        if not self.config or not self.dataset_manager or not self.client:
            self.setup()

        assert self.config is not None
        assert self.dataset_manager is not None
        assert self.client is not None

        logger.info(f"Starting benchmark suite: {self.config.suite_name}")

        # Track dual scores for agreement computation (keyword vs LLM judge)
        keyword_scores: List[float] = []
        llm_judge_scores: List[float] = []

        try:
            state = self.state_manager.state
            assert state is not None
            start_iter = state.current_iteration
            total_scenarios = len(self.dataset_manager)

            for iteration in range(start_iter, self.config.iterations + 1):
                logger.info(f"--- Iteration {iteration}/{self.config.iterations} ---")

                # Clear memory (Hard Fail if this fails - isolation broken)
                try:
                    self.client.clear_memory()
                except Exception as e:
                    raise MemoryPurgeError(
                        f"clear_memory() failed. Benchmark isolation is broken: {e}"
                    )

                start_scenario = (
                    state.current_scenario_idx if iteration == start_iter else 0
                )

                if start_scenario > 0:
                    logger.info(
                        f"  Rebuilding database state: Ingesting scenarios 0 to {start_scenario - 1} for noise parity..."
                    )
                    for rebuild_idx in range(0, start_scenario):
                        rebuild_scen = self.dataset_manager.get_scenario(rebuild_idx)
                        for ctx in rebuild_scen.contexts:
                            self._call_with_backoff(self.client.add_memory, ctx)

                for scenario_idx in range(start_scenario, total_scenarios):
                    scenario = self.dataset_manager.get_scenario(scenario_idx)
                    logger.info(
                        f"  Processing scenario {scenario_idx + 1}/{total_scenarios}: '{scenario.name}'"
                    )

                    # Ingestion phase (with backoff)
                    logger.info(f"    Ingesting {len(scenario.contexts)} contexts...")
                    for ctx in scenario.contexts:
                        self._call_with_backoff(self.client.add_memory, ctx)

                    # Query phase (per-question error handling)
                    logger.info(
                        f"    Evaluating {len(scenario.questions)} questions..."
                    )
                    for q in scenario.questions:
                        try:
                            response = self._call_with_backoff(self.client.answer, q)

                            # Dual evaluation: primary + optional secondary
                            eval_result, secondary_result = self._dual_evaluate(
                                response, q
                            )

                            logger.info(
                                f"      Q: {q.id} -> Score: {eval_result.score}, "
                                f"Latency: {eval_result.latency_ms:.2f}ms"
                            )

                            result_record = {
                                "run_id": self.run_id,
                                "iteration": iteration,
                                "scenario_id": scenario.id,
                                "question_id": q.id,
                                "score": eval_result.score,
                                "is_correct": eval_result.is_correct,
                                "latency_ms": eval_result.latency_ms,
                                "ground_truth": q.ground_truth,
                                "actual_answer": response.answer_text,
                                "expected_context_ids": q.expected_context_ids,
                                "retrieved_context_ids": response.retrieved_context_ids,
                                "prompt_tokens": response.token_usage.get("prompt", 0),
                                "evaluation_strategy": q.evaluation_strategy,
                            }

                            # Dual-scoring: record secondary score and track agreement
                            if secondary_result is not None:
                                result_record["secondary_score"] = (
                                    secondary_result.score
                                )
                                result_record["secondary_is_correct"] = (
                                    secondary_result.is_correct
                                )
                                result_record["secondary_evaluator"] = (
                                    secondary_result.metadata.get(
                                        "evaluator_type", "unknown"
                                    )
                                )
                                if q.evaluation_strategy in (
                                    "llm_judge",
                                    "multi_model_judge",
                                ):
                                    keyword_scores.append(secondary_result.score)
                                    llm_judge_scores.append(eval_result.score)
                                else:
                                    keyword_scores.append(eval_result.score)
                                    llm_judge_scores.append(secondary_result.score)

                            self._append_result(result_record)

                        except Exception as e:
                            # Per spec: ClientTimeoutError -> score=0, log, continue
                            logger.error(f"      Q: {q.id} -> FAILED: {e}")
                            fail_record = {
                                "run_id": self.run_id,
                                "iteration": iteration,
                                "scenario_id": scenario.id,
                                "question_id": q.id,
                                "score": 0.0,
                                "is_correct": False,
                                "latency_ms": None,
                                "ground_truth": q.ground_truth,
                                "actual_answer": f"ERROR: {e}",
                                "expected_context_ids": q.expected_context_ids,
                                "retrieved_context_ids": [],
                                "prompt_tokens": 0,
                                "evaluation_strategy": q.evaluation_strategy,
                            }
                            self._append_result(fail_record)

                    # Save state
                    self.state_manager.update_progress(iteration, scenario_idx + 1)
            logger.info("Benchmark execution completed.")
            self.state_manager.mark_completed()

            # Compute agreement metrics if dual-scoring was used
            agreement_data = {}
            if keyword_scores and llm_judge_scores:
                agreement_data = compute_agreement(keyword_scores, llm_judge_scores)
                logger.info(
                    "Agreement Rate: %.2f%%, Cohen's Kappa: %.4f",
                    agreement_data.get("agreement_rate", 0.0),
                    agreement_data.get("cohens_kappa", 0.0),
                )

            # Trigger Reporting
            logger.info("Calculating metrics and generating report...")
            metrics = calculate_metrics_from_jsonl(state.results_file)

            # Inject agreement data into metrics for the reporter
            metrics_dict = metrics.model_dump()
            metrics_dict["agreement"] = agreement_data

            reporter = MarkdownReporter(self.run_id, self.config)
            report_path = reporter.generate_report_from_dict(metrics_dict)

            logger.info(
                f"Benchmark finished successfully. Report generated at: {report_path}"
            )

        except MemoryPurgeError:
            logger.critical(
                "CRITICAL: Memory purge failed. Stopping benchmark immediately."
            )
            self.state_manager.mark_failed("MemoryPurgeError: isolation broken")
            raise
        except Exception as e:
            logger.error(f"Benchmark failed: {e}")
            self.state_manager.mark_failed(str(e))
            raise
        finally:
            if self.client:
                try:
                    self.client.close()
                except Exception as e:
                    logger.warning(f"Failed to cleanly close client adapter: {e}")
            logger.info(f"Benchmark run {self.run_id} completely finished.")
