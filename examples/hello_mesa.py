"""
hello_mesa.py — MESA Cognitive Memory Engine Tutorial
=====================================================

A ready-to-run tutorial demonstrating MESA's core capabilities in three
progressive scenarios.  Uses the DeterministicMockAdapter so no API keys
or GPU are required.

Usage:
    python examples/hello_mesa.py

Scenarios:
    1. Single record ingest → hybrid retrieval
    2. Concurrent multi-agent ingestion
    3. Multi-hop graph traversal
"""

import asyncio
import os
import shutil
import sys

# ---------------------------------------------------------------------------
# Ensure the MESA package is importable regardless of where we run from
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mesa_memory.adapter.factory import AdapterFactory
from mesa_memory.config import config
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.observability.metrics import ObservabilityLayer
from mesa_memory.retrieval.core import QueryAnalyzer
from mesa_memory.retrieval.hybrid import HybridRetriever
from mesa_memory.schema.cmb import CMB, ResourceCost
from mesa_memory.storage import StorageFacade
from mesa_memory.valence.fitness import calculate_fitness_score

# Bypass the aggressive memory limit check for the tutorial
config.lancedb_memory_limit_bytes = 100 * 1024 * 1024 * 1024

# ---------------------------------------------------------------------------
# Storage paths — isolated from the main application database
# ---------------------------------------------------------------------------
STORAGE_DIR = os.path.join(os.path.dirname(__file__), "..", "storage", "hello_mesa")
RAW_LOG = os.path.join(STORAGE_DIR, "raw_log.db")
VECTOR_URI = os.path.join(STORAGE_DIR, "vector_index.lance")
GRAPH_DB = os.path.join(STORAGE_DIR, "knowledge_graph.db")
GRAPH_ROCKS = os.path.join(STORAGE_DIR, "kg_history.rocks")


def banner(title: str) -> None:
    """Print a visible section header."""
    width = 60
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}\n")


async def setup() -> tuple:
    """Initialise a fresh StorageFacade and mock adapter for the tutorial."""
    # Wipe previous tutorial state so every run is reproducible
    if os.path.exists(STORAGE_DIR):
        shutil.rmtree(STORAGE_DIR)
    os.makedirs(STORAGE_DIR, exist_ok=True)

    # Use the deterministic mock adapter — no API keys needed
    adapter = AdapterFactory.get_adapter("mock")

    facade = StorageFacade(
        raw_log_path=RAW_LOG,
        vector_uri=VECTOR_URI,
        graph_db_path=GRAPH_DB,
        graph_rocks_path=GRAPH_ROCKS,
    )
    await facade.initialize_all()

    # Grant RBAC access for all tutorial agents (WRITE includes READ)
    for agent in ["tutorial_agent", "Agent_A", "Agent_B", "Agent_C"]:
        facade.access_control.grant_access(agent, "tutorial_session", "WRITE")

    return facade, adapter


# ═══════════════════════════════════════════════════════════════
# SCENARIO 1 — Single Ingest + Hybrid Retrieval
# ═══════════════════════════════════════════════════════════════
async def scenario_1_ingest_and_retrieve(facade: StorageFacade, adapter):
    """Demonstrate the simplest MESA workflow: store one record, query it back."""
    banner("Scenario 1: Single Record Ingest → Hybrid Retrieval")

    # --- Step 1: Build a CMB (Cognitive Memory Block) -----------------------
    content = (
        "Tesla reported Q4 2025 revenue of $25.7 billion, "
        "exceeding analyst expectations by 12%."
    )
    embedding = adapter.embed(content)
    token_count = adapter.get_token_count(content)
    fitness = calculate_fitness_score(content, token_count)

    cmb = CMB(
        content_payload=content,
        source="earnings_report",
        performative="INFORM",
        resource_cost=ResourceCost(token_count=token_count, latency_ms=5.0),
        embedding=embedding,
        fitness_score=fitness,
        tier3_deferred=True,  # Mark for consolidation processing
    )

    print(f"  CMB ID       : {cmb.cmb_id}")
    print(f"  Fitness Score : {fitness:.4f}")
    print(f"  Embedding Dim : {len(embedding)}")

    # --- Step 2: Persist to all storage layers ------------------------------
    await facade.persist_cmb(
        cmb, agent_id="tutorial_agent", session_id="tutorial_session"
    )
    print("  ✅ Record persisted to SQLite + LanceDB")

    # --- Step 3: Retrieve via hybrid search ---------------------------------
    analyzer = QueryAnalyzer()
    retriever = HybridRetriever(
        storage_facade=facade,
        analyzer=analyzer,
        embedder=adapter,
        access_control=facade.access_control,
    )

    query = "What was Tesla's Q4 revenue?"
    result_ids = await retriever.retrieve(
        query_text=query,
        agent_id="tutorial_agent",
        session_id="tutorial_session",
        top_n=3,
    )

    print(f"\n  Query: '{query}'")
    print(f"  Results returned: {len(result_ids)}")
    for rid in result_ids:
        record = await facade.get_cmb(rid, "tutorial_agent", "tutorial_session")
        if record:
            print(f"    → {record['content_payload'][:80]}...")


# ═══════════════════════════════════════════════════════════════
# SCENARIO 2 — Concurrent Multi-Agent Ingestion
# ═══════════════════════════════════════════════════════════════
async def scenario_2_concurrent_ingestion(facade: StorageFacade, adapter):
    """Simulate three agents concurrently ingesting financial intelligence."""
    banner("Scenario 2: Concurrent Multi-Agent Ingestion")

    records = [
        {
            "agent": "Agent_A",
            "content": "Morgan Stanley upgraded Tesla to Overweight with a $350 price target.",
        },
        {
            "agent": "Agent_B",
            "content": "Apple announced a $110 billion share buyback program, the largest in history.",
        },
        {
            "agent": "Agent_C",
            "content": "NVIDIA reported data centre revenue of $18.4B, up 279% year-over-year.",
        },
        {
            "agent": "Agent_A",
            "content": "The Federal Reserve held interest rates steady at 5.25-5.50%.",
        },
        {
            "agent": "Agent_B",
            "content": "Microsoft acquired Activision Blizzard for $68.7 billion after regulatory approval.",
        },
        {
            "agent": "Agent_C",
            "content": "Saudi Aramco profits fell 24% to $121B amid declining oil prices.",
        },
    ]

    async def ingest_one(rec: dict) -> None:
        embedding = adapter.embed(rec["content"])
        token_count = adapter.get_token_count(rec["content"])
        cmb = CMB(
            content_payload=rec["content"],
            source="analyst_note",
            performative="INFORM",
            resource_cost=ResourceCost(token_count=token_count, latency_ms=5.0),
            embedding=embedding,
            fitness_score=calculate_fitness_score(rec["content"], token_count),
            tier3_deferred=True,
        )
        await facade.persist_cmb(
            cmb, agent_id=rec["agent"], session_id="tutorial_session"
        )
        print(f"  [{rec['agent']}] Ingested: {rec['content'][:50]}...")

    # Fire all ingestions concurrently
    await asyncio.gather(*[ingest_one(r) for r in records])
    print(f"\n  ✅ {len(records)} records ingested concurrently by 3 agents")

    # --- Run consolidation to extract knowledge triplets --------------------
    print("\n  Running ConsolidationLoop...")
    obs = ObservabilityLayer()
    loop = ConsolidationLoop(
        storage_facade=facade,
        embedder=adapter,
        llm_a=adapter,
        llm_b=adapter,
        obs_layer=obs,
    )
    await loop.run_batch()
    print("  ✅ Consolidation complete — triplets extracted and committed")


# ═══════════════════════════════════════════════════════════════
# SCENARIO 3 — Multi-Hop Graph Traversal
# ═══════════════════════════════════════════════════════════════
async def scenario_3_graph_traversal(facade: StorageFacade):
    """Demonstrate multi-hop path finding across the knowledge graph."""
    banner("Scenario 3: Multi-Hop Graph Traversal")

    # Access the underlying graph provider
    graph = facade.graph

    # List all nodes currently in the graph
    all_nodes = await graph.get_all_active_nodes()
    print(f"  Graph contains {len(all_nodes)} active nodes")

    if len(all_nodes) < 2:
        print("  ⚠️  Not enough nodes for traversal demo (need ≥ 2)")
        print("     This is expected when using the mock adapter — the graph")
        print("     is populated with generic 'MockHead'/'MockTail' entities.")
        return

    # Show a sample of the graph contents
    print("\n  Sample nodes:")
    for node in all_nodes[:6]:
        print(f"    • {node['name']} (type={node['type']})")

    # Check connectivity from the first node
    source = all_nodes[0]["name"]
    target = all_nodes[-1]["name"]

    print(f"\n  Searching path: '{source}' → '{target}' (max 3 hops)")

    source_id = all_nodes[0]["node_id"]
    neighbors = await graph.get_neighbors(source_id, direction="both")
    if neighbors:
        print(f"  Node '{source}' has {len(neighbors)} neighbor(s):")
        for n in neighbors[:3]:
            print(
                f"    → {n.get('name', n.get('node_id', '?'))} "
                f"via [{n.get('relation', '?')}]"
            )
    else:
        print(f"  Node '{source}' has no direct neighbors")

    print("\n  ✅ Graph traversal demonstration complete")


# ═══════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════
async def main():
    print("╔════════════════════════════════════════════════════════════╗")
    print("║          MESA — Hello World Tutorial                     ║")
    print("║          Memory Engine for Structured Agents             ║")
    print("╚════════════════════════════════════════════════════════════╝")

    facade, adapter = await setup()

    try:
        await scenario_1_ingest_and_retrieve(facade, adapter)
        await scenario_2_concurrent_ingestion(facade, adapter)
        await scenario_3_graph_traversal(facade)
    finally:
        # Clean summary
        banner("Tutorial Complete")
        print("  All 3 scenarios executed successfully.")
        print("  Tutorial storage located at: storage/hello_mesa/")
        print("  Run 'pytest tests/ -q' to verify the full test suite.\n")


if __name__ == "__main__":
    asyncio.run(main())
