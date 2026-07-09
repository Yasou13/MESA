import time
from typing import Any, Dict

from ..datasets.schemas import BenchmarkQuestion, MemoryContext
from .base import AbstractBenchmarkClient, BenchmarkResponse

try:
    from mem0 import Memory
except ImportError:
    Memory = None


class Mem0ClientAdapter(AbstractBenchmarkClient):
    """
    Adapter for the Mem0 system (Baseline).
    """

    def __init__(self) -> None:
        self.memory = None

    def initialize(self, config_params: Dict[str, Any]) -> None:
        if Memory is None:
            raise ImportError(
                "mem0ai library is not installed. Run 'pip install mem0ai'"
            )

        # Mem0 uses a dict config
        mem0_config = config_params.get("mem0_config", {})
        self.memory = Memory.from_config(mem0_config)

    def clear_memory(self) -> None:
        if self.memory:
            self.memory.reset()

    def add_memory(self, context: MemoryContext) -> Dict[str, Any]:
        start_time = time.time()

        if self.memory:
            # Mem0 add format: add(messages, user_id=...)
            self.memory.add(
                context.text, user_id="benchmark_user", metadata={"id": context.id}
            )

        latency = (time.time() - start_time) * 1000
        return {"latency_ms": latency}

    def answer(self, question: BenchmarkQuestion) -> BenchmarkResponse:
        start_time = time.time()

        retrieved_ids: list[str] = []
        answer_text = ""

        if self.memory:
            # Search Mem0
            results = self.memory.search(question.query, user_id="benchmark_user")
            # Synthesize answer or return raw context depending on the test definition
            answer_text = str(results)

            if results:
                # Try to extract the custom ID we injected during add_memory
                for res in results:
                    meta = res.get("metadata", {})
                    if "id" in meta:
                        retrieved_ids.append(meta["id"])

        latency = (time.time() - start_time) * 1000

        return BenchmarkResponse(
            answer_text=answer_text,
            retrieved_context_ids=retrieved_ids,
            latency_ms=latency,
            metadata={"mem0": True},
        )
