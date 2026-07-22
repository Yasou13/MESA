"""
LLM-as-a-Judge Evaluator.
Uses an LLM (e.g., GPT-4o, Claude, Ollama) to evaluate system answers against ground truth.
Routes through litellm for provider-agnostic LLM calls (supports Ollama via OPENAI_BASE_URL).
"""

import concurrent.futures
import logging
import os
from typing import Optional

from ..clients.base import BenchmarkResponse
from ..datasets.schemas import BenchmarkQuestion
from .base import BaseEvaluator, EvaluationResult
from .verdict import JudgeVerdict, parse_judge_verdict

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
        quorum: Optional[int] = None,
        timeout_s: float = 120.0,
        seed: int = 42,
    ):
        self.judge_model = judge_model
        self.temperature = temperature
        self.ensemble_size = ensemble_size
        self.quorum = quorum or (ensemble_size // 2 + 1)
        if self.quorum > ensemble_size:
            raise ValueError("judge quorum cannot exceed ensemble size")
        self.timeout_s = timeout_s
        self.seed = seed

    def _call_litellm(self, prompt: str) -> Optional[dict]:
        """Calls the judge model via litellm and parses the JSON response."""
        try:
            target_model = self.judge_model

            # Auto-prefix for Ollama-routed models without a provider prefix
            if "/" not in target_model and os.environ.get("BENCHMARK_OLLAMA_URL"):
                target_model = f"openai/{target_model}"

            # Disable thinking mode for Qwen3 models to get direct JSON output
            effective_prompt = prompt
            if "qwen3" in target_model.lower():
                effective_prompt = "/no_think\n" + prompt

            ollama_host = os.environ.get("BENCHMARK_OLLAMA_URL", "")
            if ollama_host:
                import ollama

                client = ollama.Client(host=ollama_host, timeout=self.timeout_s)
                m_name = target_model.replace("openai/", "")
                resp = client.chat(
                    model=m_name,
                    messages=[{"role": "user", "content": effective_prompt}],
                    format=JudgeVerdict.model_json_schema(),
                    think=False,
                    options={"temperature": self.temperature, "seed": self.seed},
                )
                message = getattr(resp, "message", None) or resp.get("message", {})
                raw = getattr(message, "content", None) or message.get("content", "")
            else:
                import litellm

                litellm.suppress_debug_info = True
                response = litellm.completion(
                    model=target_model,
                    messages=[{"role": "user", "content": effective_prompt}],
                    temperature=self.temperature,
                    max_tokens=1024,
                    num_retries=0,
                    timeout=self.timeout_s,
                )
                raw = response.choices[0].message.content or ""

            raw = raw.strip()

            if not raw:
                logger.warning("Empty response from model=%s", self.judge_model)
                return None
            result = parse_judge_verdict(raw)
            return result.model_dump()

        except Exception as e:
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

        results = []
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.ensemble_size)
        try:
            futures = [
                executor.submit(self._call_litellm, prompt)
                for _ in range(self.ensemble_size)
            ]
            for future in concurrent.futures.as_completed(
                futures, timeout=self.timeout_s
            ):
                res = future.result(timeout=0)
                if res is not None:
                    results.append(res)
        except concurrent.futures.TimeoutError as exc:
            if len(results) < self.quorum:
                raise RuntimeError(
                    f"LLM Judge timed out before quorum ({len(results)}/{self.quorum})"
                ) from exc
        finally:
            # Judge calls have provider-native deadlines. Waiting here prevents
            # detached evaluation workers from accumulating across questions.
            executor.shutdown(wait=True, cancel_futures=True)

        if len(results) >= self.quorum:
            correct_votes = sum(1 for r in results if r.get("is_correct", False))
            avg_score = sum(float(r.get("score", 0.0)) for r in results) / len(results)
            is_correct = correct_votes > len(results) / 2
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
        logger.error("LLM Judge failed quorum (%d/%d).", len(results), self.quorum)
        raise RuntimeError(
            f"LLM Judge quorum failed ({len(results)}/{self.quorum}). "
            "Check model availability and response schema."
        )
