"""
Multi-Model LLM-as-a-Judge Evaluator.

Uses 2-3 different LLM models to independently evaluate system answers,
then computes inter-model agreement (Cohen's Kappa) and determines
the final score via majority voting.

This addresses the self-grading bias concern: no single model's judgment
is taken as ground truth.
"""

import concurrent.futures
import logging
import os
from typing import Any, Dict, List, Optional

from ..clients.base import BenchmarkResponse
from ..datasets.schemas import BenchmarkQuestion
from .base import BaseEvaluator, EvaluationResult
from .verdict import JudgeVerdict, parse_judge_verdict

logger = logging.getLogger(__name__)

JUDGE_PROMPT = """You are an expert evaluator assessing a memory-augmented RAG system.
Determine if the answer addresses the question and satisfies every applicable rubric criterion.

Question: {question}
Reference Answers: {reference_answers}
Rubric Criteria: {rubric}
System Answer: {system_answer}
Expected Context IDs: {expected_contexts}
Retrieved Context IDs: {retrieved_contexts}

Evaluate whether the retrieved context contains sufficient and accurate information.
If the system answer is raw text/JSON chunks, read through them to check if the ground truth is present.
Respond ONLY in JSON format:
{{"is_correct": true/false, "score": 0.0 to 1.0, "reasoning": "your explanation"}}"""


def _call_litellm(
    model: str, prompt: str, temperature: float = 0.0, timeout_s: float = 120.0
) -> Optional[Dict[str, Any]]:
    """Calls a single LLM model via litellm and parses the JSON response."""
    try:
        target_model = model
        if "/" not in target_model and os.environ.get("BENCHMARK_OLLAMA_URL"):
            target_model = f"openai/{target_model}"

        # Disable thinking mode for Qwen3 models to get direct JSON output
        effective_prompt = prompt
        if "qwen3" in target_model.lower():
            effective_prompt = "/no_think\n" + prompt

        ollama_host = os.environ.get("BENCHMARK_OLLAMA_URL", "")
        if ollama_host:
            import ollama

            client = ollama.Client(host=ollama_host, timeout=timeout_s)
            m_name = target_model.replace("openai/", "")
            resp = client.chat(
                model=m_name,
                messages=[{"role": "user", "content": effective_prompt}],
                format=JudgeVerdict.model_json_schema(),
                think=False,
                options={"temperature": temperature},
            )
            message = getattr(resp, "message", None) or resp.get("message", {})
            raw = getattr(message, "content", None) or message.get("content", "")

        else:
            import litellm

            litellm.suppress_debug_info = True
            response = litellm.completion(
                model=target_model,
                messages=[{"role": "user", "content": effective_prompt}],
                temperature=temperature,
                max_tokens=1024,
                num_retries=0,
                timeout=timeout_s,
            )
            raw = response.choices[0].message.content or ""

        if not raw:
            logger.warning("Empty response from model=%s", model)
            return None
        result = parse_judge_verdict(raw)
        return result.model_dump()

    except Exception as e:
        logger.warning("Multi-model judge call failed for model=%s: %s", model, e)
        return None


class MultiModelJudgeEvaluator(BaseEvaluator):
    """
    Evaluator that queries multiple LLM models and aggregates their judgments.

    Produces:
    - Individual per-model scores
    - Majority-vote final score
    - Inter-model Agreement Rate (agreement metric)
    """

    def __init__(
        self,
        judge_models: Optional[List[str]] = None,
        temperature: float = 0.0,
        timeout_s: float = 120.0,
        quorum: Optional[int] = None,
        max_concurrency: int = 3,
    ):
        configured_models = judge_models or [
            "gpt-4o-mini",
            "claude-sonnet-4-20250514",
        ]
        normalized_models = [
            model.removeprefix("openai/") for model in configured_models
        ]
        self.judge_models = list(dict.fromkeys(normalized_models))
        if len(self.judge_models) < 2:
            raise ValueError("multi-model judge requires at least two distinct models")
        self.temperature = temperature
        self.timeout_s = timeout_s
        self.max_concurrency = min(max_concurrency, len(self.judge_models))
        self.quorum = quorum or (len(self.judge_models) // 2 + 1)
        if self.quorum > len(self.judge_models):
            raise ValueError("judge quorum cannot exceed model count")

    def evaluate(
        self, response: BenchmarkResponse, question: BenchmarkQuestion
    ) -> EvaluationResult:
        """
        Queries all configured judge models, collects their verdicts,
        and returns the majority-vote result with per-model metadata.
        """
        prompt = JUDGE_PROMPT.format(
            question=question.query,
            reference_answers=question.reference_answers,
            rubric=question.rubric,
            system_answer=response.answer_text,
            expected_contexts=question.supporting_context_ids,
            retrieved_contexts=response.retrieved_context_ids,
        )

        model_results: Dict[str, Dict[str, Any]] = {}
        scores: List[float] = []
        verdicts: List[bool] = []

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_concurrency,
            thread_name_prefix="mesa-judge",
        ) as executor:
            futures = {
                executor.submit(
                    _call_litellm, model, prompt, self.temperature, self.timeout_s
                ): model
                for model in self.judge_models
            }
            for future in concurrent.futures.as_completed(
                futures, timeout=self.timeout_s
            ):
                model = futures[future]
                result = future.result()
                if result is not None:
                    score = float(result.get("score", 0.0))
                    is_correct = bool(result["is_correct"])
                    reasoning = str(result.get("reasoning", ""))
                    model_results[model] = {
                        "score": score,
                        "is_correct": is_correct,
                        "reasoning": reasoning,
                    }

        # Sort after parallel completion to keep persisted result order stable.
        for model in self.judge_models:
            result = model_results.get(model)
            if result is not None:
                scores.append(float(result["score"]))
                verdicts.append(bool(result["is_correct"]))

        # Fallback if all models failed
        if len(scores) < self.quorum:
            logger.error("All judge models failed.")
            raise RuntimeError(
                "All judge models failed to reach quorum "
                f"({len(scores)}/{self.quorum})."
            )

        # Majority vote
        correct_count = sum(1 for v in verdicts if v)
        avg_score = sum(scores) / len(scores)
        majority_correct = correct_count > len(verdicts) / 2

        # Compute inter-model agreement rate if 2+ models responded
        inter_model_agreement_rate = None
        if len(verdicts) >= 2:
            inter_model_agreement_rate = self._compute_pairwise_agreement(verdicts)

        return EvaluationResult(
            score=avg_score,
            latency_ms=response.latency_ms,
            is_correct=majority_correct,
            reasoning=f"Majority vote: {correct_count}/{len(verdicts)} models agreed correct.",
            metadata={
                "evaluator_type": "MultiModelJudgeEvaluator",
                "models_queried": [
                    model for model in self.judge_models if model in model_results
                ],
                "per_model_results": {
                    model: model_results[model]
                    for model in self.judge_models
                    if model in model_results
                },
                "majority_vote": majority_correct,
                "inter_model_agreement_rate": inter_model_agreement_rate,
            },
        )

    @staticmethod
    def _compute_pairwise_agreement(verdicts: List[bool]) -> float:
        """
        Computes pairwise agreement ratio across all model verdicts.
        Returns a value between 0.0 and 1.0.
        """
        n = len(verdicts)
        if n < 2:
            return 1.0

        agreements = 0
        total_pairs = 0
        for i in range(n):
            for j in range(i + 1, n):
                total_pairs += 1
                if verdicts[i] == verdicts[j]:
                    agreements += 1

        return agreements / total_pairs if total_pairs > 0 else 1.0
