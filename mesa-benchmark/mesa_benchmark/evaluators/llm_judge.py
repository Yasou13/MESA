"""
LLM-as-a-Judge Evaluator.
Uses an LLM (e.g., GPT-4o, Claude) to evaluate system answers against ground truth.
"""

import json
import logging
from typing import Any

from ..clients.base import BenchmarkResponse
from ..datasets.schemas import BenchmarkQuestion
from .base import BaseEvaluator, EvaluationResult

logger = logging.getLogger(__name__)

# Default prompt template for LLM judging
JUDGE_PROMPT_TEMPLATE = """You are an expert evaluator assessing a Retrieval-Augmented Generation (RAG) system's memory layer.
Your task is to determine if the retrieved context (System Answer) contains the necessary information to logically satisfy the Ground Truth.

Ground Truth: {ground_truth}
Retrieved Context (System Answer): {system_answer}

Expected Context IDs: {expected_contexts}
Retrieved Context IDs: {retrieved_contexts}

Evaluate whether the retrieved context contains sufficient and accurate information to address the question based on the ground truth.
If the system answer is just raw text/JSON chunks, that is EXPECTED. Read through them to see if the ground truth is present.
Respond in JSON format:
{{
  "is_correct": true/false,
  "score": 0.0 to 1.0,
  "reasoning": "your explanation here"
}}
"""


class LLMJudgeEvaluator(BaseEvaluator):
    """
    Evaluator that uses an LLM to judge the quality of system responses.
    Suitable for complex Multi-Hop and Contradiction scenarios where
    simple string matching is insufficient.
    """

    def __init__(self, judge_model: str = "gpt-4o", temperature: float = 0.0):
        self.judge_model = judge_model
        self.temperature = temperature
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazily initializes the LLM client."""
        if self._client is None:
            try:
                import openai

                self._client = openai.OpenAI()
            except ImportError:
                raise ImportError(
                    "openai package is required for LLMJudgeEvaluator. "
                    "Install it with: pip install openai"
                )
        return self._client

    def evaluate(
        self, response: BenchmarkResponse, question: BenchmarkQuestion
    ) -> EvaluationResult:
        """
        Sends the ground truth and system output to an LLM for evaluation.
        Falls back to a simple substring check if the LLM call fails.
        """
        prompt = JUDGE_PROMPT_TEMPLATE.format(
            ground_truth=question.ground_truth,
            system_answer=response.answer_text,
            expected_contexts=question.expected_context_ids,
            retrieved_contexts=response.retrieved_context_ids,
        )

        try:
            client = self._get_client()
            completion = client.chat.completions.create(
                model=self.judge_model,
                temperature=self.temperature,
                messages=[{"role": "user", "content": prompt}],
            )

            raw_content = completion.choices[0].message.content.strip()

            # Parse JSON response from LLM
            # Use regex to robustly extract JSON object, ignoring any surrounding text
            import re

            json_match = re.search(r"\{.*\}", raw_content, re.DOTALL)
            if json_match:
                raw_content = json_match.group(0)

            judge_result = json.loads(raw_content)

            return EvaluationResult(
                score=float(judge_result.get("score", 0.0)),
                latency_ms=response.latency_ms,
                is_correct=bool(judge_result.get("is_correct", False)),
                reasoning=str(judge_result.get("reasoning", "")),
                metadata={
                    "evaluator_type": "LLMJudgeEvaluator",
                    "judge_model": self.judge_model,
                    "raw_judge_response": raw_content,
                },
            )

        except Exception as e:
            logger.warning(f"LLM Judge failed, falling back to substring match: {e}")

            # Fallback: simple substring match
            gt = question.ground_truth.strip().lower()
            ans = response.answer_text.strip().lower()
            is_match = gt in ans

            return EvaluationResult(
                score=1.0 if is_match else 0.0,
                latency_ms=response.latency_ms,
                is_correct=is_match,
                reasoning=f"LLM Judge unavailable ({e}). Fallback substring match used.",
                metadata={
                    "evaluator_type": "LLMJudgeEvaluator",
                    "fallback": True,
                    "error": str(e),
                },
            )
