import asyncio
import hashlib
import json
import math
import os
import random
import re
import sys
from dotenv import load_dotenv

load_dotenv()

# Ensure MESA is in the python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"

# ---------------------------------------------------------------------------
# Mock REBEL extractor — generates plausible triplets from input text
# instead of returning an empty list, so the consolidation loop can
# populate the knowledge graph without requiring the real 1.8 GB model.
# ---------------------------------------------------------------------------

from mesa_memory.config import config

EMBEDDING_DIM = config.embedding_dimension  # 1536


def _deterministic_embedding(text: str) -> list[float]:
    """Generate a deterministic, unit-normalised pseudo-random embedding.

    Seeds a PRNG with the SHA-256 hash of the text so every call with
    the same text returns an identical vector, while different texts
    produce orthogonal-ish vectors — exactly what vector search needs.
    """
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16) % (2**32)
    rng = random.Random(seed)
    raw = [rng.gauss(0, 1) for _ in range(EMBEDDING_DIM)]
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / norm for x in raw]


def _mock_extract_triplets(text: str) -> list[dict]:
    """Extract pseudo-triplets by pulling capitalised entities from text.

    Strategy:
    1. Find all capitalised multi-word entities (2+ consecutive Title-Case words).
    2. Fall back to individual capitalised words if no multi-word entities found.
    3. Pair consecutive entities as (subject, object) with a generic relation
       derived from the first verb-like word between them, or 'RELATES_TO'.

    This is intentionally rough — it exists only to unblock the demo pipeline.
    """
    if not text or not text.strip():
        return []

    # Find multi-word named entities (e.g. "Elon Musk", "Morgan Stanley")
    multi_word = re.findall(r'\b(?:[A-ZÇĞİÖŞÜ][a-zçğıöşü]+(?:\s+)){1,3}[A-ZÇĞİÖŞÜ][a-zçğıöşü]+\b', text)
    # Deduplicate preserving order
    seen = set()
    entities = []
    for ent in multi_word:
        key = ent.strip().lower()
        if key not in seen:
            seen.add(key)
            entities.append(ent.strip())

    # Fallback: individual capitalised words (min 3 chars, not sentence starters)
    if len(entities) < 2:
        words = text.split()
        for i, w in enumerate(words):
            clean = re.sub(r'[^A-Za-zÇĞİÖŞÜçğıöşü]', '', w)
            if len(clean) >= 3 and clean[0].isupper() and i > 0:
                key = clean.lower()
                if key not in seen:
                    seen.add(key)
                    entities.append(clean)

    if len(entities) < 2:
        # Last resort: use first 6+ char words
        for w in text.split():
            clean = re.sub(r'[^A-Za-zÇĞİÖŞÜçğıöşü0-9]', '', w)
            if len(clean) >= 6 and clean.lower() not in seen:
                seen.add(clean.lower())
                entities.append(clean)
            if len(entities) >= 2:
                break

    if len(entities) < 2:
        return [{"head": "UnknownEntity", "relation": "MENTIONED_IN", "tail": "Document", "confidence": 0.5}]

    triplets = []
    for i in range(0, len(entities) - 1, 2):
        subj = entities[i]
        obj = entities[i + 1] if i + 1 < len(entities) else entities[0]
        # Derive a relation from context between the two entity mentions
        relation = "RELATES_TO"
        subj_pos = text.find(subj)
        obj_pos = text.find(obj)
        if subj_pos != -1 and obj_pos != -1:
            between = text[min(subj_pos + len(subj), obj_pos):max(subj_pos, obj_pos)]
            verbs = re.findall(r'\b[a-zçğıöşü]{3,12}(?:ed|ing|tion|ment|ise|ize|aldı|etti|dı|di)\b', between, re.IGNORECASE)
            if verbs:
                relation = verbs[0].upper().replace(" ", "_")
        triplets.append({
            "head": subj,
            "relation": relation,
            "tail": obj,
            "confidence": 0.9,
        })

    return triplets if triplets else [{"head": entities[0], "relation": "RELATES_TO", "tail": entities[-1], "confidence": 0.85}]


from unittest.mock import MagicMock
mock_rebel = MagicMock()
mock_extractor = MagicMock()
mock_extractor.extract_triplets.side_effect = _mock_extract_triplets
mock_rebel.RebelExtractor = MagicMock(return_value=mock_extractor)
sys.modules['mesa_memory.extraction.rebel_pipeline'] = mock_rebel

from mesa_memory.storage import StorageFacade
from mesa_memory.schema.cmb import CMB, ResourceCost, AffectiveState
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.retrieval.hybrid import HybridRetriever
from mesa_memory.retrieval.core import QueryAnalyzer
from mesa_memory.adapter.factory import AdapterFactory
from mesa_memory.observability.metrics import ObservabilityLayer

# Bypass the aggressive memory limit check for the demo
config.lancedb_memory_limit_bytes = 100 * 1024 * 1024 * 1024

async def ingest_record(facade: StorageFacade, record: dict, agent_id: str):
    content = f"[{record['record_id']}] {record['content']}"
    cmb = CMB(
        content_payload=content,
        source=record['source_type'],
        performative='INFORM',
        resource_cost=ResourceCost(token_count=50, latency_ms=10.0),
        embedding=_deterministic_embedding(content),
        tier3_deferred=True
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
    for dir_path in ["./storage/vector_index_demo.lance", "./storage/kg_history_demo.rocks"]:
        try:
            shutil.rmtree(dir_path)
        except FileNotFoundError:
            pass

    print("Initializing MESA StorageFacade...")
    facade = StorageFacade(
        raw_log_path="./storage/raw_log_demo.db",
        vector_uri="./storage/vector_index_demo.lance",
        graph_db_path="./storage/knowledge_graph_demo.db",
        graph_rocks_path="./storage/kg_history_demo.rocks"
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
    for i, record in enumerate(dataset):
        agent_id = agents[i % len(agents)]
        tasks.append(ingest_record(facade, record, agent_id))
    
    await asyncio.gather(*tasks)
    print(f"Ingested {len(dataset)} records.")

    print("Running ConsolidationLoop (Tier-3 processing)...")
    adapter = AdapterFactory.get_adapter()
    
    obs_layer = ObservabilityLayer()
    loop = ConsolidationLoop(storage_facade=facade, embedder=adapter, llm_a=adapter, llm_b=adapter, obs_layer=obs_layer)
    
    await loop.run_batch()
    print("Consolidated Tier-3 deferred records.")

    print("Submitting Deep Research Query via HybridRetriever...")
    analyzer = QueryAnalyzer()
    retriever = HybridRetriever(storage_facade=facade, analyzer=analyzer, embedder=adapter, access_control=facade.access_control)
    
    query = "Explain the Twitter Inc. Acquisition and the Morgan Stanley -> Tesla collateral multi-hop traversal."
    results_ids = await retriever.retrieve(
        query_text=query,
        agent_id="Agent_A",
        session_id="demo_session",
        top_n=5
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
            
    prompt = f"Query: {query}\n\nContext:\n" + "\n".join([c['content_payload'] for c in context])
    print("\n" + adapter.complete(prompt))
    print("==============================================")

if __name__ == "__main__":
    asyncio.run(main())
