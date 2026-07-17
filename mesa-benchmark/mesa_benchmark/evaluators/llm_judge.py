"""
LLM-as-a-Judge Evaluator.
Uses an LLM (e.g., GPT-4o, Claude, Ollama) to evaluate system answers against ground truth.
Routes through litellm for provider-agnostic LLM calls (supports Ollama via OPENAI_BASE_URL).
"""

import json
import logging
import os
from typing import Optional

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

    Uses litellm for provider-agnostic routing (OpenAI, Anthropic, Ollama, etc.).
    """

    def __init__(
        self,
        judge_model: str = "gpt-4o",
        temperature: float = 0.0,
        ensemble_size: int = 3,
    ):
        self.judge_model = judge_model
        # Use a non-zero temperature for ensemble variance, unless explicitly 0
        self.temperature = temperature if temperature > 0.0 else 0.7
        self.ensemble_size = ensemble_size

    def _call_litellm(self, prompt: str) -> Optional[dict]:
        """Calls the judge model via litellm and parses the JSON response."""
        try:
            import litellm

            litellm.suppress_debug_info = False
            litellm.set_verbose = True  # type: ignore
            target_model = self.judge_model

            # Auto-prefix for Ollama-routed models without a provider prefix
            if "/" not in target_model and "11434" in os.environ.get(
                "OPENAI_BASE_URL", ""
            ):
                target_model = f"openai/{target_model}"

            # Disable thinking mode for Qwen3 models to get direct JSON output
            effective_prompt = prompt
            if "qwen3" in target_model.lower():
                effective_prompt = "/no_think\n" + prompt

            base_url = os.environ.get("OPENAI_BASE_URL", "")
            is_litellm = False
            if "11434" in base_url:
                import ollama

                host = base_url.replace("/v1", "")
                client = ollama.Client(host=host)
                m_name = target_model.replace("openai/", "")
                resp = client.chat(
                    model=m_name,
                    messages=[{"role": "user", "content": effective_prompt}],
                    options={"temperature": self.temperature},
                )
                raw = resp.get("message", {}).get("content", "")
            else:
                is_litellm = True
                response = litellm.completion(
                    model=target_model,
                    messages=[{"role": "user", "content": effective_prompt}],
                    temperature=self.temperature,
                    max_tokens=1024,
                    num_retries=0,
                )
                raw = response.choices[0].message.content or ""

            raw = raw.strip()

            # Fallback: if content is empty, try reasoning_content (thinking models)
            if not raw and is_litellm:
                msg = response.choices[0].message
                reasoning = getattr(msg, "reasoning_content", None) or ""
                if reasoning:
                    raw = reasoning.strip()

            if not raw:
                logger.warning("Empty response from model=%s", self.judge_model)
                return None

            # Handle markdown code blocks
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]

            # Robust JSON extraction
            import re

            json_match = re.search(r"\{.*\}", raw, re.DOTALL)
            if json_match:
                raw = json_match.group(0)

            result: dict = json.loads(raw)
            return result

        except Exception as e:
            import traceback

            traceback.print_exc()
            logger.warning(
                "LLM Judge call failed for model=%s: %s", self.judge_model, e
            )
            return None

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

        import concurrent.futures

        results = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.ensemble_size
        ) as executor:
            futures = [
                executor.submit(self._call_litellm, prompt)
                for _ in range(self.ensemble_size)
            ]
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res is not None:
                    results.append(res)

        if results:
            correct_votes = sum(1 for r in results if r.get("is_correct", False))
            avg_score = sum(float(r.get("score", 0.0)) for r in results) / len(results)
            is_correct = avg_score >= 0.5
            combined_reasoning = "\n---\n".join(
                [str(r.get("reasoning", "")) for r in results]
            )

            return EvaluationResult(
                score=avg_score,
                latency_ms=response.latency_ms,
                is_correct=is_correct,
                reasoning=f"Majority Vote ({correct_votes}/{len(results)}): \n{combined_reasoning}",
                metadata={
                    "evaluator_type": "LLMJudgeEvaluator",
                    "judge_model": self.judge_model,
                    "ensemble_size": len(results),
                    "raw_judge_responses": str(results),
                },
            )

        # Fallback: Raise an error instead of silently passing
        logger.error("LLM Judge failed.")
        raise RuntimeError(
            "LLM Judge evaluation failed. Check your API keys and model availability."
        )
