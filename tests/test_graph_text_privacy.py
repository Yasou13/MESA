"""F-20: graph files contain structure; canonical SQLite supplies evidence."""

import hashlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import kuzu
import pytest

from mesa_memory.api import server
from mesa_storage.kuzu_schema_migration import migrate_schema_offline
from mesa_storage.projection_generations import ProjectionGenerationRepository
from mesa_storage.repositories.operations import OperationRepository
from mesa_storage.sqlite_engine import AsyncEngine
from tests.test_model_optional_runtime import _consume
from tests.test_model_optional_runtime import model_runtime as model_runtime
from tests.test_v4_document_privacy_runtime import (
    _grant_lifecycle_permissions,
    _insert_document,
    _purge_document,
)
from tests.test_v4_retrieval_hardening_phase9 import (
    _create_graph_env,
    _ingest_test_assertion,
)


def _graph_text_hits(graph_path, marker):
    path = Path(graph_path)
    files = [
        p
        for root in path.parent.glob(path.name + "*")
        for p in ([root] if root.is_file() else root.rglob("*"))
        if p.is_file()
    ]
    return {
        str(p): p.read_bytes().count(marker.encode())
        for p in files
        if marker.encode() in p.read_bytes()
    }


def _graph_string_hits(graph_path, marker, *, database=None):
    """Inspect logical STRING values, excluding Kuzu's unused page tails."""
    owns_database = database is None
    if owns_database:
        database = kuzu.Database(str(graph_path), read_only=True)
    connection = kuzu.Connection(database)
    try:
        with connection.execute("CALL show_tables() RETURN name, type") as result:
            tables = result.get_all()
        hits = {}
        for table, kind in tables:
            table_literal = table.replace("\\", "\\\\").replace("'", "\\'")
            with connection.execute(
                f"CALL table_info('{table_literal}') RETURN name, type"
            ) as result:
                properties = [name for name, dtype in result if dtype == "STRING"]
            if not properties:
                continue
            label = "`" + table.replace("`", "``") + "`"
            assert kind in {"NODE", "REL"}, (table, kind)
            pattern = f"(item:{label})" if kind == "NODE" else f"()-[item:{label}]->()"
            columns = ", ".join(
                "item.`" + name.replace("`", "``") + "`" for name in properties
            )
            with connection.execute(f"MATCH {pattern} RETURN {columns}") as result:
                for row in result:
                    for name, value in zip(properties, row):
                        if isinstance(value, str) and marker in value:
                            key = f"{table}.{name}"
                            hits[key] = hits.get(key, 0) + 1
        return hits
    finally:
        connection.close()
        if owns_database:
            database.close()


def test_graph_string_scan_covers_node_and_relationship_properties(tmp_path):
    path = tmp_path / "graph"
    database = kuzu.Database(str(path))
    connection = kuzu.Connection(database)
    marker = "LOGICALGRAPHPRIVACYMARKER"
    try:
        connection.execute(
            "CREATE NODE TABLE ScanNode (id STRING, text STRING, "
            "nullable_text STRING, number INT64, PRIMARY KEY (id))"
        )
        connection.execute(
            "CREATE REL TABLE ScanRel (FROM ScanNode TO ScanNode, text STRING)"
        )
        connection.execute(
            "CREATE (:ScanNode {id: 'first', text: $text, number: 42}), "
            "(:ScanNode {id: 'second', text: 'safe'})",
            {"text": f"prefix {marker} suffix"},
        )
        connection.execute(
            "MATCH (a:ScanNode {id: 'first'}), (b:ScanNode {id: 'second'}) "
            "CREATE (a)-[:ScanRel {text: $text}]->(b)",
            {"text": marker},
        )
        assert _graph_string_hits(path, marker, database=database) == {
            "ScanNode.text": 1,
            "ScanRel.text": 1,
        }
    finally:
        connection.close()
        database.close()
    assert _graph_string_hits(path, marker) == {
        "ScanNode.text": 1,
        "ScanRel.text": 1,
    }


@pytest.mark.asyncio
async def test_dao_passes_only_scoped_sqlite_evidence_to_graph(tmp_path):
    sql, graph, dao = await _create_graph_env(tmp_path)
    try:
        _, _, allowed = await _ingest_test_assertion(
            dao,
            graph,
            mutation_id="allowed",
            subject_name="Seed",
            predicate="LINKS",
            object_name="Allowed",
            evidence_span="SQLITE_EVIDENCE",
            jurisdiction="TR",
        )
        await _ingest_test_assertion(
            dao,
            graph,
            mutation_id="excluded",
            subject_name="Seed",
            predicate="LINKS",
            object_name="Excluded",
            evidence_span="EXCLUDED_EVIDENCE",
            jurisdiction="US",
        )
        await graph.execute_write("MATCH (a:Assertion) SET a.evidence_span = ''")
        with patch.object(
            graph, "search_v4_graph", wraps=graph.search_v4_graph
        ) as search:
            results = await dao.search_v4_memory(
                tenant_id="test-tenant",
                agent_id="test-agent",
                dataset_ids=["ds1"],
                query="Seed",
                jurisdiction="TR",
            )
        assert search.call_args.kwargs.get("assertion_evidence") == {
            allowed: "SQLITE_EVIDENCE"
        }
        assert results and any(r["evidence_span"] == "SQLITE_EVIDENCE" for r in results)
        assert "EXCLUDED_EVIDENCE" not in str(results)
    finally:
        await graph.close()
        await sql.close()


@pytest.mark.asyncio
async def test_purge_leaves_no_evidence_or_literal_in_graph_files(model_runtime):
    marker = "GRAPHRAWPRIVACYMARKER"
    async with model_runtime(MESA_GRAPH_ENABLED=True) as env:
        await _grant_lifecycle_permissions()
        literal = await _insert_document(
            env,
            content=f"{marker} durable memory.",
            evidence_span=marker,
            source_ref=f"source-{marker}",
            legal_identity={
                "law_id": "tbk",
                "statute_code": "TBK",
                "article_id": "344",
            },
        )
        entity = await _insert_document(
            env,
            document="graph-entity",
            content=f"{marker} entity evidence.",
            evidence_span=marker,
            source_ref=f"source-{marker}",
        )
        await _consume(env, 6)
        for mutation in (literal, entity):
            assert (await env.dao.get_mutation("model-agent", mutation))[
                "state"
            ] == "COMMITTED"
        literal_rows = await env.dao.list_v4_assertions_for_mutation(literal)
        assert marker in literal_rows[0]["literal_value"]
        results = await env.dao.search_v4_memory(
            tenant_id="model-tenant",
            agent_id="model-agent",
            dataset_ids=["main"],
            query="graph-entity",
            graph_enabled=True,
        )
        graph_results = [
            r for r in results if r["retrieval_provenance"].get("graph_paths")
        ]
        assert graph_results and any(
            r["evidence_span"] == marker for r in graph_results
        )
        graph_path = env.dao._graph.db_path
        await _purge_document(env, "privacy")
        await _purge_document(env, "graph-entity")
        assert _graph_string_hits(
            graph_path, marker, database=env.dao._graph._db
        ) == {}
        await env.stop()
        assert _graph_string_hits(graph_path, marker) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("hops", [1, 2, 3])
async def test_sqlite_evidence_preserves_graph_scores_and_order(tmp_path, hops):
    sql, graph, dao = await _create_graph_env(tmp_path)
    try:
        entities, assertions = set(), set()
        subject = "Seed"
        for hop in range(1, hops + 1):
            s, o, a = await _ingest_test_assertion(
                dao,
                graph,
                mutation_id=f"hop-{hop}",
                subject_name=subject,
                predicate="LINKS",
                object_name=f"Target-{hop}",
                evidence_span="focus TBK m.117" if hop == hops else "",
                jurisdiction="",
            )
            entities.update((s, o))
            assertions.add(a)
            if hop == 1:
                seed = s
            subject = f"Target-{hop}"
        target = o
        _, noise, noise_a = await _ingest_test_assertion(
            dao,
            graph,
            mutation_id="noise",
            subject_name="Seed",
            predicate="LINKS",
            object_name="Noise",
            jurisdiction="",
        )
        entities.add(noise)
        assertions.add(noise_a)
        async with sql.connection() as db:
            rows = await (
                await db.execute(
                    "SELECT assertion_id, evidence_span FROM v4_assertions"
                )
            ).fetchall()
        evidence = {row[0]: row[1] for row in rows}
        arguments = dict(
            agent_id="test-agent",
            seed_entity_ids=[seed],
            allowed_entity_ids=entities,
            allowed_assertion_ids=assertions,
            max_hops=hops,
            limit=10,
            query="focus TBK m.117",
            direction="forward",
            assertion_evidence=evidence,
        )
        baseline = await graph.search_v4_graph(**arguments)
        baseline_scores = [(hit["entity_id"], hit["score"]) for hit in baseline]
        expected = 2.3 / (1 + 0.5 * (hops - 1))
        assert dict(baseline_scores)[target] == pytest.approx(expected)
        assert [hit[0] for hit in baseline_scores].index(target) < [
            hit[0] for hit in baseline_scores
        ].index(noise)
        await graph.execute_write("MATCH (a:Assertion) SET a.evidence_span = ''")
        after = await graph.search_v4_graph(**arguments)
        assert [(hit["entity_id"], hit["score"]) for hit in after] == baseline_scores
        arguments["assertion_evidence"] = {}
        missing = await graph.search_v4_graph(**arguments)
        assert {hit["entity_id"]: hit["score"] for hit in missing}[
            target
        ] == pytest.approx(1 / (1 + 0.5 * (hops - 1)))
    finally:
        await graph.close()
        await sql.close()


@pytest.mark.asyncio
async def test_schema_migration_does_not_copy_legacy_assertion_text(tmp_path):
    sql, graph, dao = await _create_graph_env(tmp_path)
    marker = "MIGRATIONGRAPHPRIVACYMARKER"
    try:
        await _ingest_test_assertion(
            dao,
            graph,
            mutation_id="legacy",
            subject_name="Seed",
            predicate="LINKS",
            object_name="Target",
        )
        await graph.execute_write(
            "MATCH (a:Assertion) SET a.evidence_span = $text, a.object_value = $text, a.source_ref = $text",
            {"text": marker},
        )
        path = Path(graph.db_path)
    finally:
        await graph.close()
        await sql.close()
    assert _graph_text_hits(path, marker)
    migrate_schema_offline(path)
    assert _graph_string_hits(path, marker) == {}


@pytest.mark.asyncio
async def test_rebuild_creates_text_free_graph_from_legacy_store(
    model_runtime, tmp_path
):
    marker = "REBUILDGRAPHPRIVACYMARKER"
    async with model_runtime(MESA_V4_REBUILD_ENABLED=True, MESA_GRAPH_ENABLED=True) as env:
        await _insert_document(
            env, content=f"{marker} durable memory.", evidence_span=marker
        )
        await _consume(env, 3)
        old_path = Path(env.dao._graph.db_path)
        # Simulate an existing deployment even after new writes are text-free.
        await env.dao._graph.execute_write(
            "MATCH (a:Assertion) SET a.evidence_span = $text", {"text": marker}
        )
        storage = server.state.runtime_profile.storage_root
        await env.stop()
        assert _graph_text_hits(old_path, marker)
        sql = AsyncEngine(str(storage / "mesa.db"))
        await sql.initialize()
        try:
            submitted = await OperationRepository(sql).submit(
                requested_by_principal_id="model-principal",
                idempotency_key="text-free-rebuild",
                payload_hash=hashlib.sha256(b"text-free-rebuild").hexdigest(),
            )
        finally:
            await sql.close()
        work = tmp_path / "rebuild-work"
        work.mkdir()
        # The supported offline runner starts after the runtime process exits.
        rebuilt = subprocess.run(
            [
                sys.executable,
                "-m",
                "mesa_memory.rebuild_runner",
                "run",
                "--trusted-root",
                str(tmp_path),
                "--storage-root",
                str(storage),
                "--work-root",
                str(work),
                "--operation-id",
                str(submitted["operation_id"]),
            ],
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
        sql = AsyncEngine(str(storage / "mesa.db"))
        await sql.initialize()
        try:
            active = await ProjectionGenerationRepository(sql).resolve_active(
                storage_root=storage, trusted_root=tmp_path
            )
        finally:
            await sql.close()
        assert active.graph_path != old_path
        assert active.graph_path.is_file()
        assert _graph_string_hits(active.graph_path, marker) == {}
        await env.restart()
        results = await env.dao.search_v4_memory(
            tenant_id="model-tenant",
            agent_id="model-agent",
            dataset_ids=["main"],
            query="privacy",
        )
        assert any(r["evidence_span"] == marker for r in results)
