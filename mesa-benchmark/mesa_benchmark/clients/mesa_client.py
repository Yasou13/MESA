import time
from typing import Any, Dict

from ..datasets.schemas import BenchmarkQuestion, MemoryContext
from .base import AbstractBenchmarkClient, BenchmarkResponse

try:
    from mesa_memory.dao.memory_dao import MemoryDAO

    from mesa_memory.config import MesaConfig
except ImportError:
    # If not installed as a package, try adjusting the path or handle gracefully.
    import os
    import sys

    # Add parent directory of mesa_benchmark to path to find mesa_memory
    sys.path.append(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
    )
    from mesa_memory.dao.memory_dao import MemoryDAO

    from mesa_memory.config import MesaConfig


class MesaClientAdapter(AbstractBenchmarkClient):
    """
    Adapter for the MESA framework.
    Translates benchmark requests into MESA MemoryDAO calls.
    """

    def __init__(self) -> None:
        self.memory_dao: Any = None
        self.config: Any = None

    def initialize(self, config_params: Dict[str, Any]) -> None:
        """Initializes MESA components."""
        # Typically MESA config is loaded from environment or a config file.
        # We can construct a MesaConfig from parameters if needed.
        self.config = MesaConfig()

        # Override config with benchmark parameters
        if "vector_db" in config_params:
            assert self.config is not None
            self.config.vector_db_type = config_params["vector_db"]

        self.memory_dao = MemoryDAO(config=self.config)

    def clear_memory(self) -> None:
        """Flushes the database for a clean test environment."""
        if self.memory_dao:
            # Assuming MESA has a clear or reset method.
            # If not, you might need to drop tables manually or use a test database.
            try:
                self.memory_dao.reset_database()
            except AttributeError:
                # Fallback if reset is not natively supported
                pass

    def add_memory(self, context: MemoryContext) -> Dict[str, Any]:
        """Ingests context into MESA."""
        start_time = time.time()

        # Add metadata and context text to MESA memory
        if self.memory_dao:
            self.memory_dao.insert_memory(
                text=context.text,
                entity_name=context.id,  # Or extracted from metadata
                embedding=[],  # Real embedding generation should be handled by MESA or passed.
            )

        latency = (time.time() - start_time) * 1000
        return {"latency_ms": latency}

    def answer(self, question: BenchmarkQuestion) -> BenchmarkResponse:
        """Queries MESA and returns the response."""
        start_time = time.time()

        retrieved_ids: list[str] = []
        answer_text = ""

        if self.memory_dao:
            # We mock the retrieval part if the real router isn't available,
            # or call memory_dao.search
            results = self.memory_dao.search_memory(question.query, top_k=5)
            # answer_text = "Generated from MESA based on results"
            # In a real integration, the LLM synthesis step of MESA would be called.
            answer_text = str(results)

        latency = (time.time() - start_time) * 1000

        return BenchmarkResponse(
            answer_text=answer_text,
            retrieved_context_ids=retrieved_ids,
            latency_ms=latency,
            metadata={"mesa_version": "0.5.1"},
        )
