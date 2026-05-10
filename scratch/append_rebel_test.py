import sys
import os

test_file = "/home/yasin/Desktop/MESA/tests/test_consolidation.py"

with open(test_file, "r") as f:
    content = f.read()

new_test = """
@pytest.mark.asyncio
async def test_rebel_extraction_fallback():
    obs = ObservabilityLayer()
    storage = MagicMock()
    storage.raw_log = MagicMock()
    
    # One record
    record = {"cmb_id": "r-1", "content_payload": "Alice likes Bob.", "source": "agent"}
    storage.raw_log.fetch_unconsolidated = AsyncMock(return_value=[record])
    storage.raw_log.mark_consolidated = AsyncMock()
    storage.graph = MagicMock()
    storage.graph.upsert_node = AsyncMock(return_value="n_id")
    storage.graph.create_edge = AsyncMock(return_value="e_id")
    
    llm_a = MagicMock()
    llm_b = MagicMock()
    
    # Mock LLM return to verify fallback
    llm_a.complete.return_value = json.dumps({"head": "Alice", "relation": "likes", "tail": "Bob"})
    llm_b.complete.return_value = json.dumps({"head": "Alice", "relation": "likes", "tail": "Bob"})
    embedder = MagicMock()

    loop_obj = ConsolidationLoop(
        storage_facade=storage,
        embedder=embedder,
        llm_a=llm_a,
        llm_b=llm_b,
        obs_layer=obs,
    )
    
    # Force Rebel Extractor to fail
    loop_obj.rebel_extractor.extract_triplets = MagicMock(return_value=[])

    with patch("mesa_memory.consolidation.loop.calculate_composite_similarity", return_value=0.9):
        await loop_obj.run_batch([record])
        
    # Assert LLM WAS called because rebel failed
    assert llm_a.complete.called
    assert llm_b.complete.called

@pytest.mark.asyncio
async def test_rebel_extraction_success():
    obs = ObservabilityLayer()
    storage = MagicMock()
    storage.raw_log = MagicMock()
    
    record = {"cmb_id": "r-2", "content_payload": "Alice likes Bob.", "source": "agent"}
    storage.raw_log.fetch_unconsolidated = AsyncMock(return_value=[record])
    storage.raw_log.mark_consolidated = AsyncMock()
    storage.graph = MagicMock()
    storage.graph.upsert_node = AsyncMock(return_value="n_id")
    storage.graph.create_edge = AsyncMock(return_value="e_id")
    
    llm_a = MagicMock()
    llm_b = MagicMock()
    embedder = MagicMock()

    loop_obj = ConsolidationLoop(
        storage_facade=storage,
        embedder=embedder,
        llm_a=llm_a,
        llm_b=llm_b,
        obs_layer=obs,
    )
    
    # Force Rebel Extractor to succeed
    loop_obj.rebel_extractor.extract_triplets = MagicMock(return_value=[{"head": "Alice", "relation": "likes", "tail": "Bob"}])

    with patch("mesa_memory.consolidation.loop.calculate_composite_similarity", return_value=0.9):
        await loop_obj.run_batch([record])
        
    # Assert LLM WAS NOT called because rebel succeeded
    assert not llm_a.complete.called
    assert not llm_b.complete.called
    assert storage.graph.create_edge.called
"""

if "test_rebel_extraction_fallback" not in content:
    with open(test_file, "a") as f:
        f.write(new_test)
        print("Tests appended successfully")
else:
    print("Tests already exist")
