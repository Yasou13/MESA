import asyncio
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Ensure MESA is in the python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"

from mesa_memory.adapter.factory import AdapterFactory  # noqa: E402
from mesa_memory.config import config  # noqa: E402
from mesa_memory.consolidation.loop import ConsolidationLoop  # noqa: E402
from mesa_memory.observability.metrics import ObservabilityLayer  # noqa: E402
from mesa_memory.retrieval.core import QueryAnalyzer  # noqa: E402
from mesa_memory.retrieval.hybrid import HybridRetriever  # noqa: E402
from mesa_memory.schema.cmb import CMB, ResourceCost  # noqa: E402
from mesa_memory.storage import StorageFacade  # noqa: E402

# Bypass the aggressive memory limit check for the demo
config.lancedb_memory_limit_bytes = 100 * 1024 * 1024 * 1024


async def ingest_record(facade: StorageFacade, adapter, record: dict, agent_id: str):
    content = f"[{record['record_id']}] {record['content']}"
    cmb = CMB(
        content_payload=content,
        source=record["source_type"],
        performative="INFORM",
        resource_cost=ResourceCost(token_count=50, latency_ms=10.0),
        embedding=adapter.embed(content),
        tier3_deferred=True,
    )
    await facade.persist_cmb(cmb, agent_id, session_id="demo_session")
    print(f"[{agent_id}] Ingested {record['record_id']}")


async def main():
    import shutil

    print("Wiping existing databases to prevent state pollution...")
    for db_path in ["./storage/raw_log_demo.db", "./storage/knowledge_graph_demo.db"]:
        try:
            os.remove(db_path)
        except FileNotFoundError:
            pass
    for dir_path in [
        "./storage/vector_index_demo.lance",
        "./storage/kg_history_demo.rocks",
    ]:
        try:
            shutil.rmtree(dir_path)
        except FileNotFoundError:
            pass

    print("Initializing MESA StorageFacade...")
    facade = StorageFacade(
        raw_log_path="./storage/raw_log_demo.db",
        vector_uri="./storage/vector_index_demo.lance",
        graph_db_path="./storage/knowledge_graph_demo.db",
        graph_rocks_path="./storage/kg_history_demo.rocks",
    )
    await facade.initialize_all()

    facade.access_control.grant_access("Agent_A", "demo_session", "WRITE")
    facade.access_control.grant_access("Agent_B", "demo_session", "WRITE")
    facade.access_control.grant_access("Agent_C", "demo_session", "WRITE")

    print("Loading JSON Dataset...")
    try:
        with open("data/raw/ma_dataset.json", "r") as f:
            dataset = json.load(f)
    except Exception as e:
        print(f"Error loading JSON dataset: {e}")
        return

    print("Concurrent ingestion using multiple agents...")
    agents = ["Agent_A", "Agent_B", "Agent_C"]
    tasks = []
    adapter = AdapterFactory.get_adapter("mock")
    for i, record in enumerate(dataset):
        agent_id = agents[i % len(agents)]
        tasks.append(ingest_record(facade, adapter, record, agent_id))

    await asyncio.gather(*tasks)
    print(f"Ingested {len(dataset)} records.")

    print("Running ConsolidationLoop (Tier-3 processing)...")

    obs_layer = ObservabilityLayer()
    loop = ConsolidationLoop(
        storage_facade=facade,
        embedder=adapter,
        llm_a=adapter,
        llm_b=adapter,
        obs_layer=obs_layer,
    )

    await loop.run_batch()
    print("Consolidated Tier-3 deferred records.")

    print("Submitting Deep Research Query via HybridRetriever...")
    analyzer = QueryAnalyzer()
    retriever = HybridRetriever(
        storage_facade=facade,
        analyzer=analyzer,
        embedder=adapter,
        access_control=facade.access_control,
    )

    query = "Explain the Twitter Inc. Acquisition and the Morgan Stanley -> Tesla collateral multi-hop traversal."
    results_ids = await retriever.retrieve(
        query_text=query, agent_id="Agent_A", session_id="demo_session", top_n=5
    )

    print("\n================ FINAL REPORT ================")
    print(f"Query: {query}")
    print("Found Results:")
    context = []
    for res_id in results_ids:
        cmb = await facade.get_cmb(res_id, "Agent_A", "demo_session")
        if cmb:
            print(f"- {cmb['content_payload']} (Source: {cmb['source']})")
            context.append(cmb)

    prompt = f"Query: {query}\n\nContext:\n" + "\n".join(
        [c["content_payload"] for c in context]
    )
    print("\n" + adapter.complete(prompt))
    print("==============================================")


if __name__ == "__main__":
    asyncio.run(main())
