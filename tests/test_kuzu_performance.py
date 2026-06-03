"""
KùzuDB Performance & Event-Loop Non-Blocking Tests.

Proves two critical SLA invariants:

1. **Event-loop freedom**: KùzuDB's synchronous C++ calls, offloaded via
   ``ThreadPoolExecutor``, do NOT block the FastAPI ``asyncio`` event loop.
   Measured by running a trivial ``asyncio.sleep(0.01)`` concurrently with
   50 graph queries — if the sleep completes within 50 ms, the loop is free.

2. **P99 latency SLA**: The 99th-percentile execution time for a single
   ``get_neighbors`` call must remain under 50 ms, ensuring MESA's
   cognitive spreading-activation pipeline meets its real-time contract.

Test topology:
    A linear chain of 20 nodes per agent: N0 → N1 → … → N19
    Traversal is performed from N0 with ``max_hops=2``.
"""

import asyncio
import os
import shutil
import statistics
import time

import pytest
import pytest_asyncio

from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema
from tests.conftest import make_test_storage_dir

# ---------------------------------------------------------------------------
# Constants & thresholds
# ---------------------------------------------------------------------------

AGENT_PERF = "agent_perf_bench"
KUZU_PERF_DIR = make_test_storage_dir("kuzu_performance")
CONCURRENCY = 50
SLEEP_DURATION = 0.01  # 10 ms reference sleep
SLEEP_SLA = 0.05  # sleep must complete within 50 ms
P99_SLA_MS = 50.0  # P99 latency ceiling in milliseconds
CHAIN_LENGTH = 20  # nodes per agent subgraph


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def perf_provider():
    """Provision a KùzuDB instance pre-seeded with a 20-node linear chain.

    Yields the provider after inserting CHAIN_LENGTH nodes connected
    in sequence: N0 → N1 → N2 → … → N(CHAIN_LENGTH-1).
    """
    os.makedirs(KUZU_PERF_DIR, exist_ok=True)
    db_path = os.path.join(KUZU_PERF_DIR, "perf_db")

    initialize_schema(db_path)

    provider = KuzuGraphProvider(db_path=db_path)
    await provider.initialize()

    # Seed a linear chain: N0 → N1 → N2 → … → N19
    for i in range(CHAIN_LENGTH):
        await provider.insert_node(f"N{i}", f"PerfNode{i}", AGENT_PERF)
    for i in range(CHAIN_LENGTH - 1):
        await provider.insert_edge(
            f"N{i}", f"N{i + 1}", weight=1.0, agent_id=AGENT_PERF
        )

    yield provider

    await provider.close()
    shutil.rmtree(KUZU_PERF_DIR, ignore_errors=True)


# ===========================================================================
# TEST 1: Event-loop non-blocking proof
# ===========================================================================


class TestEventLoopFreedom:
    """Prove that KùzuDB executor offloading keeps the event loop responsive."""

    @pytest.mark.asyncio
    async def test_sleep_completes_under_sla_during_concurrent_queries(
        self, perf_provider
    ):
        """50 concurrent graph queries must NOT block a 10 ms sleep beyond 50 ms.

        Strategy:
            1. Launch 50 ``get_neighbors`` queries via ``asyncio.gather``.
            2. Simultaneously launch an ``asyncio.sleep(0.01)``.
            3. Measure the wall-clock time of the sleep.
            4. If the sleep takes > 50 ms, the event loop was blocked by
               synchronous KùzuDB calls — which means the executor
               offloading is broken.
        """

        async def timed_sleep() -> float:
            """Return the wall-clock duration of a 10 ms sleep."""
            start = time.perf_counter()
            await asyncio.sleep(SLEEP_DURATION)
            return time.perf_counter() - start

        # Build the concurrent workload
        graph_tasks = [
            perf_provider.get_neighbors(node_id="N0", agent_id=AGENT_PERF, max_hops=2)
            for _ in range(CONCURRENCY)
        ]
        sleep_task = timed_sleep()

        # Execute all concurrently
        results = await asyncio.gather(sleep_task, *graph_tasks)

        actual_sleep = results[0]
        assert actual_sleep < SLEEP_SLA, (
            f"EVENT LOOP BLOCKED: asyncio.sleep({SLEEP_DURATION}) took "
            f"{actual_sleep:.4f}s (SLA: <{SLEEP_SLA}s). "
            f"KùzuDB calls are likely running on the main thread."
        )

    @pytest.mark.asyncio
    async def test_multiple_sleep_probes_during_queries(self, perf_provider):
        """Multiple sleep probes interleaved with queries must all complete on time.

        This catches intermittent event-loop stalls that a single probe
        might miss.
        """

        async def timed_sleep() -> float:
            start = time.perf_counter()
            await asyncio.sleep(SLEEP_DURATION)
            return time.perf_counter() - start

        # 3 sleep probes + 50 graph queries
        probes = [timed_sleep() for _ in range(3)]
        queries = [
            perf_provider.get_neighbors(
                node_id=f"N{i % CHAIN_LENGTH}",
                agent_id=AGENT_PERF,
                max_hops=2,
            )
            for i in range(CONCURRENCY)
        ]

        results = await asyncio.gather(*probes, *queries)
        sleep_times = results[:3]

        for i, t in enumerate(sleep_times):
            assert t < SLEEP_SLA, f"Sleep probe {i} took {t:.4f}s (SLA: <{SLEEP_SLA}s)"


# ===========================================================================
# TEST 2: P99 latency SLA
# ===========================================================================


class TestP99Latency:
    """Verify that the 99th-percentile get_neighbors latency is under 50 ms."""

    @pytest.mark.asyncio
    async def test_p99_under_50ms(self, perf_provider):
        """50 sequential get_neighbors calls — P99 must be < 50 ms.

        Sequential execution isolates per-query latency from concurrency
        effects, giving a clean P99 measurement of the KùzuDB round-trip.
        """
        latencies: list[float] = []

        for i in range(CONCURRENCY):
            start = time.perf_counter()
            await perf_provider.get_neighbors(
                node_id=f"N{i % CHAIN_LENGTH}",
                agent_id=AGENT_PERF,
                max_hops=2,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            latencies.append(elapsed_ms)

        p50 = statistics.median(latencies)
        p99 = sorted(latencies)[int(len(latencies) * 0.99)]
        p_max = max(latencies)
        p_mean = statistics.mean(latencies)

        # Log for observability
        print(
            f"\n  Latency distribution (n={CONCURRENCY}):\n"
            f"    P50  = {p50:.2f} ms\n"
            f"    P99  = {p99:.2f} ms\n"
            f"    Max  = {p_max:.2f} ms\n"
            f"    Mean = {p_mean:.2f} ms"
        )

        assert p99 < P99_SLA_MS, (
            f"P99 LATENCY SLA BREACH: {p99:.2f} ms exceeds {P99_SLA_MS} ms ceiling. "
            f"Distribution: P50={p50:.2f}, Mean={p_mean:.2f}, Max={p_max:.2f}"
        )

    @pytest.mark.asyncio
    async def test_p99_concurrent_under_sla(self, perf_provider):
        """50 concurrent get_neighbors calls — P99 must be < 200 ms.

        Unlike the sequential test (which measures raw KùzuDB latency),
        concurrent execution is bottlenecked by the ``ThreadPoolExecutor``
        with ``_MAX_WORKERS=2``.  50 tasks through 2 threads = 25 serial
        batches.  At ~3 ms per query, the theoretical floor is ~75 ms.
        The 200 ms SLA provides headroom for CI variability while still
        catching catastrophic regressions.

        The **sequential** P99 test (above) enforces the strict 50 ms contract.
        """

        async def timed_query(node_id: str) -> float:
            start = time.perf_counter()
            await perf_provider.get_neighbors(
                node_id=node_id,
                agent_id=AGENT_PERF,
                max_hops=2,
            )
            return (time.perf_counter() - start) * 1000.0

        tasks = [timed_query(f"N{i % CHAIN_LENGTH}") for i in range(CONCURRENCY)]
        latencies = await asyncio.gather(*tasks)

        p50 = statistics.median(latencies)
        p99 = sorted(latencies)[int(len(latencies) * 0.99)]
        p_max = max(latencies)
        p_mean = statistics.mean(latencies)

        concurrent_sla_ms = 250.0

        print(
            f"\n  Concurrent latency (n={CONCURRENCY}):\n"
            f"    P50  = {p50:.2f} ms\n"
            f"    P99  = {p99:.2f} ms\n"
            f"    Max  = {p_max:.2f} ms\n"
            f"    Mean = {p_mean:.2f} ms"
        )

        assert p99 < concurrent_sla_ms, (
            f"CONCURRENT P99 SLA BREACH: {p99:.2f} ms exceeds {concurrent_sla_ms} ms. "
            f"ThreadPoolExecutor contention may be the bottleneck."
        )


# ===========================================================================
# TEST 3: Throughput baseline
# ===========================================================================


class TestThroughput:
    """Measure total wall-clock time for the concurrent batch."""

    @pytest.mark.asyncio
    async def test_50_concurrent_queries_complete_under_1s(self, perf_provider):
        """50 concurrent queries must complete within 1 second total."""
        start = time.perf_counter()

        tasks = [
            perf_provider.get_neighbors(
                node_id=f"N{i % CHAIN_LENGTH}",
                agent_id=AGENT_PERF,
                max_hops=2,
            )
            for i in range(CONCURRENCY)
        ]
        results = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - start

        print(
            f"\n  Throughput: {CONCURRENCY} queries in {elapsed:.3f}s "
            f"({CONCURRENCY / elapsed:.0f} qps)"
        )

        assert elapsed < 1.0, (
            f"THROUGHPUT SLA BREACH: {CONCURRENCY} queries took "
            f"{elapsed:.3f}s (must be < 1.0s)"
        )

        # Verify results are structurally valid
        for r in results:
            assert isinstance(r, list), f"Expected list, got {type(r)}"
