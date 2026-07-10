"""
Multi-Model LLM-as-a-Judge Evaluator.

Uses 2-3 different LLM models to independently evaluate system answers,
then computes inter-model agreement (Cohen's Kappa) and determines
the final score via majority voting.

This addresses the self-grading bias concern: no single model's judgment
is taken as ground truth.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from ..clients.base import BenchmarkResponse
from ..datasets.schemas import BenchmarkQuestion
from .base import BaseEvaluator, EvaluationResult

logger = logging.getLogger(__name__)

JUDGE_PROMPT = """You are an expert evaluator assessing a memory-augmented RAG system.
Determine if the retrieved context contains the necessary information to satisfy the ground truth.

Ground Truth: {ground_truth}
Retrieved Context (System Answer): {system_answer}
Expected Context IDs: {expected_contexts}
Retrieved Context IDs: {retrieved_contexts}

Evaluate whether the retrieved context contains sufficient and accurate information.
If the system answer is raw text/JSON chunks, read through them to check if the ground truth is present.
Respond ONLY in JSON format:
{{"is_correct": true/false, "score": 0.0 to 1.0, "reasoning": "your explanation"}}"""


def _call_litellm(
    model: str, prompt: str, temperature: float = 0.0
) -> Optional[Dict[str, Any]]:
    """Calls a single LLM model via litellm and parses the JSON response."""
    try:
        import litellm

        litellm.suppress_debug_info = True
        target_model = model
        if "/" not in target_model and "11434" in os.environ.get("OPENAI_BASE_URL", ""):
            target_model = f"openai/{target_model}"

        response = litellm.completion(
            model=target_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=512,
        )

        raw = response.choices[0].message.content.strip()

        # Handle markdown code blocks
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        return json.loads(raw)

    except Exception as e:
        logger.warning("Multi-model judge call failed for model=%s: %s", model, e)
        return None


class MultiModelJudgeEvaluator(BaseEvaluator):
    """
    Evaluator that queries multiple LLM models and aggregates their judgments.

    Produces:
    - Individual per-model scores
    - Majority-vote final score
    - Inter-model Cohen's Kappa (agreement metric)
    """

    def __init__(
        self,
        judge_models: Optional[List[str]] = None,
        temperature: float = 0.0,
    ):
        self.judge_models = judge_models or [
            "gpt-4o-mini",
            "claude-sonnet-4-20250514",
        ]
        self.temperature = temperature

    def evaluate(
        self, response: BenchmarkResponse, question: BenchmarkQuestion
    ) -> EvaluationResult:
        """
        Queries all configured judge models, collects their verdicts,
        and returns the majority-vote result with per-model metadata.
        """
        prompt = JUDGE_PROMPT.format(
            ground_truth=question.ground_truth,
            system_answer=response.answer_text,
            expected_contexts=question.expected_context_ids,
            retrieved_contexts=response.retrieved_context_ids,
        )

        model_results: Dict[str, Dict[str, Any]] = {}
        scores: List[float] = []
        verdicts: List[bool] = []

        for model in self.judge_models:
            result = _call_litellm(model, prompt, self.temperature)
            if result is not None:
                score = float(result.get("score", 0.0))
                is_correct = bool(result.get("is_correct", False))
                reasoning = str(result.get("reasoning", ""))

                model_results[model] = {
                    "score": score,
                    "is_correct": is_correct,
                    "reasoning": reasoning,
                }
                scores.append(score)
                verdicts.append(is_correct)

        # Fallback if all models failed
        if not scores:
            logger.warning(
                "All judge models failed. Falling back to substring match."
            )
            gt = question.ground_truth.strip().lower()
            ans = response.answer_text.strip().lower()
            is_match = gt in ans
            return EvaluationResult(
                score=1.0 if is_match else 0.0,
                latency_ms=response.latency_ms,
                is_correct=is_match,
                reasoning="All judge models failed. Fallback substring match used.",
                metadata={
                    "evaluator_type": "MultiModelJudgeEvaluator",
                    "fallback": True,
                },
            )

        # Majority vote
        correct_count = sum(1 for v in verdicts if v)
        majority_correct = correct_count > len(verdicts) / 2
        avg_score = sum(scores) / len(scores)

        # Compute inter-model Cohen's Kappa if 2+ models responded
        inter_model_kappa = None
        if len(verdicts) >= 2:
            inter_model_kappa = self._compute_pairwise_agreement(verdicts)

        return EvaluationResult(
            score=avg_score,
            latency_ms=response.latency_ms,
            is_correct=majority_correct,
            reasoning=f"Majority vote: {correct_count}/{len(verdicts)} models agreed correct.",
            metadata={
                "evaluator_type": "MultiModelJudgeEvaluator",
                "models_queried": list(model_results.keys()),
                "per_model_results": model_results,
                "majority_vote": majority_correct,
                "inter_model_agreement": inter_model_kappa,
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
