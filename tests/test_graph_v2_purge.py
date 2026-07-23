"""Graph V2 assertion invalidation uses isolated staging storage."""

import pytest

from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema_artifact


@pytest.mark.asyncio
async def test_purge_removes_assertions_attached_to_purged_entity(tmp_path) -> None:
    graph_path = tmp_path / "graph"
    initialize_schema_artifact(str(graph_path))
    provider = KuzuGraphProvider(str(graph_path), max_workers=1)
    await provider.initialize()
    try:
        await provider.insert_node("subject", "Subject", "tenant-a")
        await provider.insert_node("object", "Object", "tenant-a")
        await provider.insert_assertion(
            assertion_id="assertion-1",
            subject_id="subject",
            object_id="object",
            agent_id="tenant-a",
            predicate="KNOWS",
            mutation_id="mutation-1",
            source_ref="source-1",
        )
        await provider.delete_nodes(
            purge_id="purge-1", agent_id="tenant-a", node_ids=["subject"]
        )
        assertions = await provider.execute_query(
            "MATCH (a:Assertion {agent_id: $agent_id}) RETURN a.id",
            {"agent_id": "tenant-a"},
        )
        assert assertions == []
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_graph_v2_preserves_literal_assertion_value(tmp_path) -> None:
    graph_path = tmp_path / "literal-graph"
    initialize_schema_artifact(str(graph_path))
    provider = KuzuGraphProvider(str(graph_path), max_workers=1)
    await provider.initialize()
    try:
        await provider.insert_node("law", "KVKK", "tenant-a")
        await provider.insert_assertion(
            assertion_id="assertion-literal",
            subject_id="law",
            object_id=None,
            object_value="6698",
            agent_id="tenant-a",
            predicate="KANUN_NUMARASI",
            mutation_id="mutation-1",
            source_ref="resmi-gazete",
        )
        rows = await provider.execute_query(
            "MATCH (a:Assertion {agent_id: $agent_id}) "
            "RETURN a.predicate, a.object_value",
            {"agent_id": "tenant-a"},
        )
        assert rows == [["KANUN_NUMARASI", "6698"]]
    finally:
        await provider.close()
