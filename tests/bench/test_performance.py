"""
MESA Performance Benchmark Suite.

Baseline benchmarks for core subsystem throughput and latency:

1. **Ingestion Throughput**:  Measures time to persist a batch of CMB records
   through RawLogStorage (SQLite) and VectorStorage (LanceDB).
2. **Graph Write Throughput**:  Measures time to upsert nodes and create edges
   through the NetworkXProvider (in-memory + SQLite persistence).
3. **Retrieval Latency**:  Measures query latency on an initialized vector store.
4. **Consolidation Parsing**:  Measures the response parsing/recovery pipeline
   throughput for batch extraction responses.

All benchmarks use real storage instances against temp directories to capture
true I/O characteristics.  No mocks are used.

Run:
    pytest tests/bench/ -v --benchmark-only
    pytest tests/bench/ -v --benchmark-json=bench_results.json
"""

import asyncio
import json
import os
import shutil
from unittest.mock import patch

import pytest

from mesa_memory.consolidation.loop import (
    _estimate_salience,
    _salvage_truncated_json,
    _sanitize_llm_response,
)
from mesa_memory.schema.cmb import CMB, ResourceCost
from mesa_memory.security.rbac import AccessControl
from mesa_memory.security.rbac_constants import SYSTEM_AGENT_ID, SYSTEM_SESSION_ID
from mesa_memory.storage.graph.networkx_provider import NetworkXProvider
from mesa_memory.storage.raw_log import RawLogStorage
from mesa_memory.storage.vector_index import VectorStorage

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BENCH_STORAGE_DIR = "./storage_bench_tmp"
EMBEDDING_DIM = 384  # Matches MiniLM-L6-v2 production embedding output
BATCH_SIZE = 20


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def bench_storage_dir():
    """Create and tear down a temp storage directory for each test."""
    os.makedirs(BENCH_STORAGE_DIR, exist_ok=True)
    yield
    shutil.rmtree(BENCH_STORAGE_DIR, ignore_errors=True)


def _make_cmb(index: int) -> CMB:
    """Generate a deterministic CMB record for benchmarking."""
    return CMB(
        content_payload=f"Benchmark record {index}: Entity_{index} acquired Target_{index} for ${index * 100}M in a strategic expansion move.",
        source=f"bench_source_{index}",
        performative="assert",
        resource_cost=ResourceCost(token_count=50 + index, latency_ms=10.0 + index),
        fitness_score=0.5 + (index % 5) * 0.1,
        embedding=[float(i % 7) * 0.1 for i in range(EMBEDDING_DIM)],
    )


def _make_record_dict(index: int) -> dict:
    """Generate a raw record dict (as returned by fetch_unconsolidated)."""
    return {
        "cmb_id": f"bench-cmb-{index:04d}",
        "content_payload": f"Company_{index} announced a merger with Target_{index} valued at ${index * 50}M.",
        "source": f"bench_source_{index}",
        "performative": "assert",
        "resource_cost": {"token_count": 50, "latency_ms": 10.0},
        "fitness_score": 0.5 + (index % 5) * 0.1,
        "embedding": [float(i % 7) * 0.1 for i in range(EMBEDDING_DIM)],
    }


def _make_batch_response_json(size: int) -> str:
    """Generate a valid BatchExtractionResponse JSON string."""
    triplets = []
    for i in range(size):
        triplets.append(
            {
                "record_index": i,
                "head": f"Entity_{i}",
                "relation": "acquired",
                "tail": f"Target_{i}",
                "confidence": 0.85 + (i % 3) * 0.05,
            }
        )
    return json.dumps({"triplets": triplets})


# ---------------------------------------------------------------------------
# 1. Ingestion Throughput — RawLogStorage (SQLite)
# ---------------------------------------------------------------------------


class TestIngestionThroughput:
    """Benchmark CMB record persistence to SQLite via RawLogStorage."""

    @pytest.fixture
    def raw_log(self):
        db_path = os.path.join(BENCH_STORAGE_DIR, "bench_raw_log.db")
        storage = RawLogStorage(db_path=db_path)
        asyncio.get_event_loop().run_until_complete(storage.initialize())
        return storage

    def test_single_cmb_insert(self, benchmark, raw_log):
        """Baseline: single CMB insert latency."""

        def _insert():
            cmb = _make_cmb(0)  # Fresh UUID7 per call
            asyncio.get_event_loop().run_until_complete(raw_log.insert_cmb(cmb))

        benchmark.pedantic(_insert, iterations=1, rounds=50)

    def test_batch_cmb_insert(self, benchmark, raw_log):
        """Throughput: sequential insert of BATCH_SIZE records."""

        def _insert_batch():
            loop = asyncio.get_event_loop()
            for i in range(BATCH_SIZE):
                cmb = _make_cmb(i)  # Fresh UUID7 per call
                loop.run_until_complete(raw_log.insert_cmb(cmb))

        benchmark.pedantic(_insert_batch, iterations=1, rounds=10)

    def test_fetch_unconsolidated(self, benchmark, raw_log):
        """Read latency: fetch unconsolidated records after batch insert."""
        records = [_make_cmb(i) for i in range(BATCH_SIZE)]
        loop = asyncio.get_event_loop()
        for cmb in records:
            loop.run_until_complete(raw_log.insert_cmb(cmb))

        def _fetch():
            loop.run_until_complete(raw_log.fetch_unconsolidated(limit=BATCH_SIZE))

        benchmark.pedantic(_fetch, iterations=1, rounds=50)


# ---------------------------------------------------------------------------
# 2. Vector Storage Throughput — LanceDB
# ---------------------------------------------------------------------------


class TestVectorThroughput:
    """Benchmark vector upsert and search operations."""

    @pytest.fixture
    def vector_store(self):
        uri = os.path.join(BENCH_STORAGE_DIR, "bench_vector.lance")
        ac = AccessControl(policy_path=os.path.join(BENCH_STORAGE_DIR, "bench_rbac.db"))
        ac.grant_access(SYSTEM_AGENT_ID, SYSTEM_SESSION_ID, "WRITE")
        vs = VectorStorage(uri=uri, access_control=ac)
        return vs

    def test_single_vector_upsert(self, benchmark, vector_store):
        """Baseline: single vector upsert latency."""
        embedding = [float(i % 7) * 0.1 for i in range(EMBEDDING_DIM)]

        def _upsert():
            with patch.object(vector_store, "_check_memory_limit"):
                vector_store.upsert_vector(
                    cmb_id="bench-vec-single",
                    embedding=embedding,
                    content_payload="Benchmark content",
                    source="bench",
                    agent_id=SYSTEM_AGENT_ID,
                    session_id=SYSTEM_SESSION_ID,
                )

        benchmark.pedantic(_upsert, iterations=1, rounds=30)

    def test_batch_vector_upsert(self, benchmark, vector_store):
        """Throughput: sequential upsert of BATCH_SIZE vectors."""
        embeddings = [
            [float((i + j) % 7) * 0.1 for j in range(EMBEDDING_DIM)]
            for i in range(BATCH_SIZE)
        ]

        def _upsert_batch():
            with patch.object(vector_store, "_check_memory_limit"):
                for i, emb in enumerate(embeddings):
                    vector_store.upsert_vector(
                        cmb_id=f"bench-vec-{i:04d}",
                        embedding=emb,
                        content_payload=f"Benchmark content {i}",
                        source="bench",
                        agent_id=SYSTEM_AGENT_ID,
                        session_id=SYSTEM_SESSION_ID,
                    )

        benchmark.pedantic(_upsert_batch, iterations=1, rounds=5)

    def test_vector_search_latency(self, benchmark, vector_store):
        """Retrieval latency: kNN search after pre-populating the store."""
        with patch.object(vector_store, "_check_memory_limit"):
            for i in range(BATCH_SIZE):
                vector_store.upsert_vector(
                    cmb_id=f"bench-search-{i:04d}",
                    embedding=[float((i + j) % 7) * 0.1 for j in range(EMBEDDING_DIM)],
                    content_payload=f"Searchable content {i}",
                    source="bench",
                    agent_id=SYSTEM_AGENT_ID,
                    session_id=SYSTEM_SESSION_ID,
                )

        query = [0.5] * EMBEDDING_DIM

        def _search():
            vector_store.search(query, limit=10)

        benchmark.pedantic(_search, iterations=1, rounds=30)


# ---------------------------------------------------------------------------
# 3. Graph Write Throughput — NetworkXProvider
# ---------------------------------------------------------------------------


class TestGraphThroughput:
    """Benchmark node upsert and edge creation through the async provider."""

    @pytest.fixture
    def provider(self):
        db_path = os.path.join(BENCH_STORAGE_DIR, "bench_kg.db")
        rocks_path = os.path.join(BENCH_STORAGE_DIR, "bench_kg.rocks")
        p = NetworkXProvider(db_path=db_path, rocks_path=rocks_path)
        asyncio.get_event_loop().run_until_complete(p.initialize())
        return p

    def test_single_node_upsert(self, benchmark, provider):
        """Baseline: single node upsert latency (in-memory + SQLite)."""

        def _upsert():
            asyncio.get_event_loop().run_until_complete(
                provider.upsert_node("BenchEntity", "ENTITY")
            )

        benchmark.pedantic(_upsert, iterations=1, rounds=50)

    def test_batch_node_upsert(self, benchmark, provider):
        """Throughput: upsert BATCH_SIZE unique nodes."""

        def _upsert_batch():
            loop = asyncio.get_event_loop()
            for i in range(BATCH_SIZE):
                loop.run_until_complete(provider.upsert_node(f"Entity_{i}", "ENTITY"))

        benchmark.pedantic(_upsert_batch, iterations=1, rounds=10)

    def test_edge_creation_throughput(self, benchmark, provider):
        """Throughput: create edges between pre-existing nodes."""
        loop = asyncio.get_event_loop()
        node_ids = []
        for i in range(BATCH_SIZE):
            nid = loop.run_until_complete(
                provider.upsert_node(f"EdgeNode_{i}", "ENTITY")
            )
            node_ids.append(nid)

        def _create_edges():
            for i in range(len(node_ids) - 1):
                loop.run_until_complete(
                    provider.create_edge(node_ids[i], node_ids[i + 1], f"rel_{i}")
                )

        benchmark.pedantic(_create_edges, iterations=1, rounds=10)

    def test_graph_query_latency(self, benchmark, provider):
        """Read latency: find_nodes_by_name + get_neighbors on populated graph."""
        loop = asyncio.get_event_loop()
        ids = []
        for i in range(BATCH_SIZE):
            nid = loop.run_until_complete(
                provider.upsert_node(f"QueryNode_{i}", "ENTITY")
            )
            ids.append(nid)
        for i in range(len(ids) - 1):
            loop.run_until_complete(provider.create_edge(ids[i], ids[i + 1], "linked"))

        def _query():
            nodes = loop.run_until_complete(
                provider.find_nodes_by_name(
                    ["QueryNode_0", "QueryNode_5", "QueryNode_10"],
                    case_insensitive=True,
                )
            )
            for n in nodes:
                loop.run_until_complete(
                    provider.get_neighbors(n["node_id"], direction="both")
                )

        benchmark.pedantic(_query, iterations=1, rounds=30)

    def test_pagerank_latency(self, benchmark, provider):
        """Analytics latency: PageRank computation on populated graph."""
        loop = asyncio.get_event_loop()
        ids = []
        for i in range(BATCH_SIZE):
            nid = loop.run_until_complete(provider.upsert_node(f"PRNode_{i}", "ENTITY"))
            ids.append(nid)
        # Create a ring topology
        for i in range(len(ids)):
            loop.run_until_complete(
                provider.create_edge(ids[i], ids[(i + 1) % len(ids)], "ring")
            )

        def _pagerank():
            loop.run_until_complete(provider.compute_pagerank())

        benchmark.pedantic(_pagerank, iterations=1, rounds=20)


# ---------------------------------------------------------------------------
# 4. Consolidation Parsing — Response Recovery Pipeline
# ---------------------------------------------------------------------------


class TestConsolidationParsing:
    """Benchmark the multi-layer JSON parsing/recovery pipeline."""

    def test_sanitize_clean_json(self, benchmark):
        """Baseline: sanitize a well-formed JSON response."""
        raw = _make_batch_response_json(BATCH_SIZE)

        benchmark(_sanitize_llm_response, raw)

    def test_sanitize_markdown_wrapped(self, benchmark):
        """Sanitize JSON wrapped in markdown code fences."""
        inner = _make_batch_response_json(BATCH_SIZE)
        raw = f"```json\n{inner}\n```"

        benchmark(_sanitize_llm_response, raw)

    def test_salvage_truncated_json(self, benchmark):
        """Recovery: salvage a truncated batch response."""
        full = _make_batch_response_json(BATCH_SIZE)
        # Truncate at ~70% to simulate max_tokens cutoff
        truncated = full[: int(len(full) * 0.7)]

        benchmark(_salvage_truncated_json, truncated)

    def test_salience_estimation(self, benchmark):
        """Throughput: salience scoring for batch ordering."""
        records = [_make_record_dict(i) for i in range(BATCH_SIZE)]

        def _score_all():
            for r in records:
                _estimate_salience(r)

        benchmark(_score_all)
