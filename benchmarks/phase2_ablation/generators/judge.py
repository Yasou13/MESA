import json
import logging

import litellm
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("MESA_LLMJudge")


class JudgeResult(BaseModel):
    is_correct: bool
    reasoning: str


class LLMJudgeEvaluator:
    """Strict LLM Judge using UniversalProvider (LiteLLM) to evaluate MESA responses."""

    def __init__(self, model: str = "groq/llama-3.1-8b-instant"):
        self.model = model
        self.system_prompt = (
            "You are an impartial AI judge evaluating RAG conflict resolution. "
            "Does the 'System Response' fully and logically satisfy the 'Expected Truth' "
            "given the historical context? Ignore formatting, focus on semantic accuracy."
            "\nOutput strictly as a JSON object with exactly two keys: 'is_correct' (boolean) and 'reasoning' (string)."
        )

    @retry(
        stop=stop_after_attempt(10),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        reraise=True,
    )
    async def evaluate(
        self,
        context_t0: str,
        context_t1: str,
        query: str,
        expected_ground_truth: str,
        actual_system_response: str,
    ) -> JudgeResult:

        user_prompt = (
            f"Context T0:\n{context_t0}\n\n"
            f"Context T1:\n{context_t1}\n\n"
            f"Query:\n{query}\n\n"
            f"Expected Truth:\n{expected_ground_truth}\n\n"
            f"System Response:\n{actual_system_response}\n\n"
            "Evaluate if the System Response correctly resolves the query according to the Expected Truth."
        )

        response = await litellm.acompletion(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )

        try:
            content = response.choices[0].message.content
            if isinstance(content, str):
                data = json.loads(content)
                return JudgeResult(**data)
            return JudgeResult(**content)
        except Exception as e:
            logger.error(f"Failed to parse Judge JSON output: {e}")
            raise ValueError(f"Judge output unparseable: {e}")
