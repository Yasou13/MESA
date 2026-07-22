import time
from dataclasses import dataclass

from ..clients.base import BenchmarkResponse
from ..datasets.schemas import BenchmarkQuestion


@dataclass(frozen=True)
class GenerationResult:
    answer: str
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int


class OllamaAnswerGenerator:
    """Common answer generator used after every system's retrieval step."""

    def __init__(
        self,
        *,
        host: str,
        model: str,
        timeout_s: float,
        temperature: float,
        seed: int,
    ) -> None:
        if not host or not model:
            raise ValueError("Ollama host and generator model are required")
        import ollama

        self.client = ollama.Client(host=host, timeout=timeout_s)
        self.model = model
        self.temperature = temperature
        self.seed = seed

    def generate(
        self, response: BenchmarkResponse, question: BenchmarkQuestion
    ) -> GenerationResult:
        contexts = response.retrieved_contexts
        if contexts:
            context_text = "\n\n".join(
                f"[{item.rank}] {item.text}" for item in contexts if item.text
            )
        else:
            context_text = response.answer_text
        prompt = (
            "Answer the question using only the retrieved context. "
            "If the answer is absent, say that it is unknown.\n\n"
            f"Context:\n{context_text}\n\nQuestion: {question.query}\nAnswer:"
        )
        started = time.perf_counter()
        raw = self.client.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            think=False,
            options={"temperature": self.temperature, "seed": self.seed},
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        message = getattr(raw, "message", None) or raw.get("message", {})
        answer = str(
            getattr(message, "content", None) or message.get("content", "")
        ).strip()
        if not answer:
            raise RuntimeError("common generator returned an empty answer")
        return GenerationResult(
            answer=answer,
            latency_ms=latency_ms,
            prompt_tokens=int(
                getattr(raw, "prompt_eval_count", None)
                or raw.get("prompt_eval_count", 0)
                or 0
            ),
            completion_tokens=int(
                getattr(raw, "eval_count", None) or raw.get("eval_count", 0) or 0
            ),
        )
