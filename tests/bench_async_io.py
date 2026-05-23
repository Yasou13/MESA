import asyncio
import os
import time
import uuid

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.observability.metrics import ObservabilityLayer
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine


# Mock LLM Adapter
class MockLLMAdapter(BaseUniversalLLMAdapter):
    def __init__(self, name: str, latency: float = 0.05):
        self.name = name
        self.latency = latency
        self.success_count = 0
        self.error_count = 0

    async def acomplete(self, prompt: str, **kwargs) -> str:
        await asyncio.sleep(self.latency)
        self.success_count += 1
        return '{"decision": "STORE", "justification": "Mock"}'

    async def generate(self, prompt: str) -> str:
        return await self.acomplete(prompt)

    async def aembed(self, text: str) -> list[float]:
        return [0.0] * 1536

    async def get_embedding(self, text: str) -> list[float]:
        return await self.aembed(text)

    async def aembed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1536 for _ in texts]

    def complete(self, prompt: str, **kwargs) -> str:
        self.success_count += 1
        return '{"decision": "STORE", "justification": "Mock"}'

    def embed(self, text: str) -> list[float]:
        return [0.0] * 1536

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1536 for _ in texts]

    def get_token_count(self, text: str) -> int:
        return len(text.split())


# Mock Vector Engine
class MockVectorEngine(VectorEngine):
    def __init__(self):
        super().__init__(":memory:")

    async def initialize(self):
        pass

    async def upsert(self, **kwargs):
        pass

    async def bulk_upsert(self, records: list[dict]):
        pass

    async def search(self, **kwargs):
        return []

    async def delete(self, **kwargs):
        pass

    async def health_check(self):
        return {"status": "ok"}

    async def close(self):
        pass


async def heartbeat(stop_event: asyncio.Event, latencies: list[float]):
    """Track max event loop latency."""
    last_time = time.perf_counter()
    while not stop_event.is_set():
        await asyncio.sleep(0.01)
        current = time.perf_counter()
        latencies.append((current - last_time - 0.01) * 1000.0)  # ms
        last_time = current


async def run_benchmark():
    print("Initializing benchmark...", flush=True)
    db_path = f"bench_{uuid.uuid4().hex}.sqlite"
    sql_engine = AsyncEngine(db_path)
    await sql_engine.initialize()
    await initialize_schema(sql_engine)

    vec_engine = MockVectorEngine()
    dao = MemoryDAO(sql_engine, vec_engine)

    small_llm = MockLLMAdapter("small_llm", latency=0.01)
    llm_a = MockLLMAdapter("llm_a", latency=0.05)
    llm_b = MockLLMAdapter("llm_b", latency=0.05)
    obs_layer = ObservabilityLayer()

    loop = ConsolidationLoop(
        dao=dao, embedder=llm_a, llm_a=llm_a, llm_b=llm_b, obs_layer=obs_layer
    )
    loop.router.small_llm = small_llm

    # Pre-insert 500 unconsolidated records
    agent_id = "bench_agent"
    print("Pre-inserting 500 records...", flush=True)
    records = []
    for i in range(500):
        records.append(
            {
                "node_id": str(uuid.uuid4()),
                "entity_name": f"TestEntity_{i}",
                "content": "Test content",
                "embedding": [0.0] * 1536,
                "tier3_deferred": True,  # force validation path
            }
        )
    await dao.bulk_insert_memory(agent_id, records=records)

    unconsolidated = await dao.get_memories(
        agent_id, include_consolidated=False, limit=500
    )
    print(f"Fetched {len(unconsolidated)} records for validation.", flush=True)

    latencies = []
    stop_event = asyncio.Event()
    hb_task = asyncio.create_task(heartbeat(stop_event, latencies))

    start_time = time.perf_counter()

    async def process_record(record):
        try:
            await loop._validate_with_timeout(record)
            return "SUCCESS"
        except Exception as e:
            return f"ERROR: {type(e).__name__}"

    print("Firing 500 concurrent validation tasks...", flush=True)
    tasks = [asyncio.create_task(process_record(rec)) for rec in unconsolidated]
    results = await asyncio.gather(*tasks)

    end_time = time.perf_counter()
    stop_event.set()
    await hb_task

    await sql_engine.close()
    if os.path.exists(db_path):
        os.remove(db_path)

    duration = end_time - start_time
    throughput = 500 / duration
    max_latency = max(latencies) if latencies else 0.0

    successes = sum(1 for r in results if r == "SUCCESS")
    errors = 500 - successes
    error_rate = (errors / 500.0) * 100

    print("\n==========================================")
    print("BENCHMARK RESULTS")
    print("==========================================")
    print("Total Tasks: 500")
    print(f"Duration: {duration:.2f} seconds")
    print(f"Throughput: {throughput:.2f} tasks/sec")
    print(f"Event Loop Max Latency: {max_latency:.2f} ms")
    print(f"Successful Validations: {successes}")
    print(f"Errors/Timeouts: {errors}")
    print(f"Error Rate: {error_rate:.2f}%")
    print("==========================================")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
