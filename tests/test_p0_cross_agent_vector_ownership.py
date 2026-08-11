import pytest

from mesa_storage.vector_engine import VectorEngine


@pytest.mark.asyncio
async def test_cross_agent_vector_isolation(tmp_path):
    """Verify that vector records sharing a node_id across different agents do NOT overwrite each other."""
    lance_path = tmp_path / "test_cross_agent_vec.lance"
    engine = VectorEngine(uri=str(lance_path))
    await engine.initialize()

    shared_node_id = "node_shared_identity"
    embedding_a = [0.1] * 384
    embedding_b = [0.9] * 384

    # Agent A upserts shared_node_id
    await engine.upsert(
        node_id=shared_node_id,
        agent_id="agent_A",
        embedding=embedding_a,
        content_hash="hash_a",
    )

    # Agent B upserts SAME shared_node_id
    await engine.upsert(
        node_id=shared_node_id,
        agent_id="agent_B",
        embedding=embedding_b,
        content_hash="hash_b",
    )

    # Search for Agent A -> must return Agent A's record only
    res_a = await engine.search(query_vector=embedding_a, agent_id="agent_A", limit=10)
    assert len(res_a) == 1
    assert res_a[0]["node_id"] == shared_node_id
    assert res_a[0]["agent_id"] == "agent_A"
    assert res_a[0]["content_hash"] == "hash_a"

    # Search for Agent B -> must return Agent B's record only
    res_b = await engine.search(query_vector=embedding_b, agent_id="agent_B", limit=10)
    assert len(res_b) == 1
    assert res_b[0]["node_id"] == shared_node_id
    assert res_b[0]["agent_id"] == "agent_B"
    assert res_b[0]["content_hash"] == "hash_b"

    await engine.close()
