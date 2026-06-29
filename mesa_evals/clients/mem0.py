# MESA v0.5.1 — Mem0 Baseline Wrapper (Mock)
from mesa_evals.clients.barerag import BareRAGClient
from mesa_memory.adapter.base import BaseUniversalLLMAdapter


class Mem0Client(BareRAGClient):
    """Mock wrapper for Mem0 baseline testing.

    Acts identically to BareRAG for benchmark testing since the mem0
    library is not natively installed in the current environment.
    """

    def __init__(self, adapter: BaseUniversalLLMAdapter):
        super().__init__(
            adapter=adapter, storage_root="./storage/benchmark_mem0", search_limit=5
        )
