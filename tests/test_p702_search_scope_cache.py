"""D37: scope-cache identity, isolation, invalidation and bounded reuse."""

import asyncio

import aiosqlite
import pytest

from mesa_storage.dao import MemoryDAO
from tests.test_model_optional_runtime import model_runtime as model_runtime
from tests.test_v4_bm25_scope_isolation import _environment
from tests.test_v4_retrieval_hardening_phase1 import _create_committed_mutation

QUERIES = [
    "kira bedeli",
    "TBK m. 314",
    "Türk Borçlar Kanunu 315",
    "ödeme süresi",
    "kiracı temerrüt",
    "rent payment",
    "compliance privacy",
    "Article 12",
    "6098 sayılı Kanun madde 314",
    "TBK 315 ihtar",
    "bedel ödenmezse",
    "veri koruma",
    "KİRA",
    "kira kira kira",
    '"kira bedeli"',
    "kira OR süre",
    "otuz gün",
    "2025",
    "m. 314/1",
    "TBK",
    "HMK m. 12",
    "İİK 269",
    "borçlar",
    "source chunk",
    "protection officer",
    "madde",
    "!!!",
    "a",
    "1",
    "🙂",
]


def _search(dao, query="kira bedeli", **kwargs):
    return dao.search_v4_memory(
        tenant_id=kwargs.pop("tenant_id", "t"),
        agent_id=kwargs.pop("agent_id", "a"),
        dataset_ids=kwargs.pop("dataset_ids", ["d"]),
        query=query,
        certification_metadata=kwargs.pop("certification_metadata", {}),
        **kwargs,
    )


async def _seed(dao, number, tenant="t", dataset="d", agent="a"):
    return await _create_committed_mutation(
        dao,
        raw_log_id=number,
        tenant_id=tenant,
        dataset_id=dataset,
        agent_id=agent,
        document_id=f"doc-{number}",
        chunk_id=f"chunk-{number}",
        content=f"TBK m. 314 kira bedeli ödeme süresi otuz gün. Article 12 privacy compliance. {number}",
        subject=f"TBK kira {number}",
        predicate="regulates_article",
        literal_value="Kira bedeli ve temerrüt ihtarı",
        evidence_span="TBK m. 315 kiracı kira bedeli ödeme süresi otuz gün.",
    )


@pytest.fixture
async def scope_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MESA_SEARCH_SCOPE_CACHE", "true")
    sql, vector, graph, dao = await _environment(tmp_path)
    await _seed(dao, 1)
    await _seed(dao, 2)
    monkeypatch.setenv("MESA_SEARCH_SCOPE_CACHE", "false")
    uncached = MemoryDAO(sql, vector, graph)
    monkeypatch.setenv("MESA_SEARCH_SCOPE_CACHE", "true")
    try:
        yield sql, dao, uncached
    finally:
        await sql.close()


def _trace(monkeypatch):
    statements = []
    execute = aiosqlite.Connection.execute

    def traced(self, sql, parameters=None):
        statements.append(sql)
        return execute(self, sql, parameters)

    monkeypatch.setattr(aiosqlite.Connection, "execute", traced)
    return statements


def _builds(statements):
    return [
        sql
        for sql in statements
        if "CREATE VIRTUAL TABLE temp.v4_scoped_assertions_fts" in sql
    ]


@pytest.mark.asyncio
async def test_30_query_results_scores_provenance_and_audits_are_identical(scope_env):
    _sql, cached, uncached = scope_env
    for query in QUERIES:
        before, after = {}, {}
        expected = await _search(uncached, query, certification_metadata=before)
        assert await _search(cached, query, certification_metadata=after) == expected
        assert after == before
        assert await _search(cached, query) == expected
    # Response mutation cannot contaminate cached rows or subsequent provenance.
    result = await _search(cached)
    result[0]["provenance"][0]["evidence_span"] = "client modification"
    assert await _search(cached) == await _search(uncached)


@pytest.mark.asyncio
async def test_unchanged_scope_does_not_rebuild_or_reload(scope_env, monkeypatch):
    _sql, cached, _uncached = scope_env
    statements = _trace(monkeypatch)
    first = await _search(cached)
    assert len(_builds(statements)) == 1
    statements.clear()
    assert await _search(cached) == first
    assert not _builds(statements)
    assert not any(
        "INSERT INTO temp.v4_scoped_assertions_fts" in sql for sql in statements
    )
    assert not any("SELECT a.* FROM v4_assertions a WHERE" in sql for sql in statements)
    assert any("PRAGMA data_version" in sql for sql in statements)


@pytest.mark.asyncio
async def test_data_version_invalidates_on_insert_and_unrelated_commit(
    scope_env, monkeypatch
):
    sql, cached, uncached = scope_env
    await _search(cached)
    statements = _trace(monkeypatch)
    await _seed(cached, 3)
    assert await _search(cached) == await _search(uncached)
    assert len(await _search(cached)) == 3
    assert len(_builds(statements)) == 2  # one cached rebuild, one uncached build
    statements.clear()
    async with sql.transaction() as db:
        await db.execute(
            "INSERT INTO system_config(key, value) VALUES ('cache-test', 'changed')"
        )
        await db.commit()
    await _search(cached)
    assert len(_builds(statements)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "foreign", [("other", "d", "a"), ("t", "other", "a"), ("t", "d", "other")]
)
async def test_scope_identity_isolates_tenants_datasets_agents(
    scope_env, monkeypatch, foreign
):
    _sql, cached, uncached = scope_env
    await _seed(cached, 3, *foreign)
    statements = _trace(monkeypatch)
    base = await _search(cached)
    foreign_result = await _search(
        cached, tenant_id=foreign[0], dataset_ids=[foreign[1]], agent_id=foreign[2]
    )
    assert base and foreign_result
    assert {r["assertion_id"] for r in base}.isdisjoint(
        r["assertion_id"] for r in foreign_result
    )
    assert len(_builds(statements)) == 2
    statements.clear()
    assert await _search(cached) == base == await _search(uncached)
    assert len(_builds(statements)) == 1  # only uncached builds


@pytest.mark.asyncio
async def test_filters_and_four_scope_lru(scope_env, monkeypatch):
    _sql, cached, uncached = scope_env
    scopes = [
        {},
        {"jurisdiction": "TR"},
        {"valid_at": "2024-01-01"},
        {"valid_from": "2020-01-01"},
        {"valid_to": "2030-01-01"},
    ]
    for scope in scopes:
        assert await _search(cached, **scope) == await _search(uncached, **scope)
    statements = _trace(monkeypatch)
    await _search(cached, **scopes[1])  # retained
    assert not _builds(statements)
    await _search(cached, **scopes[0])  # fifth scope evicted this one
    assert len(_builds(statements)) == 1
    async with cached._sql.search_connection() as db:
        async with db.execute(
            "SELECT name FROM sqlite_temp_master WHERE type='table' AND name GLOB 'v4_scoped_assertions_fts_*' AND sql LIKE '%USING fts5%'"
        ) as cur:
            assert len(await cur.fetchall()) <= 4


@pytest.mark.asyncio
async def test_concurrent_searches_are_serialized_and_connection_closes(scope_env):
    sql, cached, uncached = scope_env
    expected = [await _search(uncached, query) for query in QUERIES[:8]]
    assert (
        await asyncio.gather(*(_search(cached, query) for query in QUERIES[:8]))
        == expected
    )
    db = sql._search_db
    assert db is not None
    await sql.close()
    with pytest.raises(ValueError, match="no active connection"):
        await db.execute("SELECT 1")


@pytest.mark.asyncio
async def test_lifecycle_and_grant_changes_cannot_serve_stale_results(model_runtime):
    from mesa_memory.api import server
    from tests.test_document_ingestion import _add
    from tests.test_model_optional_runtime import _consume

    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:

        async def search():
            return await env.client.post(
                "/v4/memory/search",
                json={"session_id": env.session_id, "query": "durable memory"},
            )

        await _add(env, "Durable memory old rule.")
        await _consume(env, 3)
        first = (await search()).json()["results"]
        await _add(env, "Durable memory new rule.")
        await _consume(env, 3)
        second = (await search()).json()["results"]
        assert second and second != first
        assert "old rule" not in repr(second)
        # Roll back the successor through the actual lifecycle API.
        async with env.dao._sql.connection() as db:
            async with db.execute(
                "SELECT pipeline_run_id FROM memory_mutations ORDER BY rowid DESC LIMIT 1"
            ) as cur:
                run = (await cur.fetchone())[0]
        await env.dao.request_pipeline_rollback(run)
        rolled = (await search()).json()["results"]
        assert "new rule" not in repr(rolled)
        await env.dao.purge_v4_document(
            tenant_id="model-tenant", dataset_id="main", document_id="whole-document"
        )
        assert (await search()).json()["results"] == []
        # Grants live in the separate RBAC store; every HTTP request must
        # re-authorize even if the main database scope cache is still valid.
        await _add(
            env, "Durable memory authorized evidence.", document_id="grant-document"
        )
        await _consume(env, 3)
        assert (await search()).json()["results"]
        await server.state.access_control.revoke_scope_role(
            "model-principal",
            tenant_id="model-tenant",
            workspace_id="default",
            dataset_id="main",
        )
        assert (await search()).status_code == 403


@pytest.mark.asyncio
async def test_commit_during_search_uses_one_snapshot_then_invalidates(
    scope_env, monkeypatch
):
    sql, cached, _uncached = scope_env
    baseline = await _search(cached)
    original = MemoryDAO._search_v4_memory_uncached
    injected = False

    async def concurrent_commit(self, **kwargs):
        nonlocal injected
        if self is cached and not injected:
            injected = True
            async with sql.transaction() as db:
                await db.execute("UPDATE memory_mutations SET state='ROLLED_BACK'")
                await db.commit()
        return await original(self, **kwargs)

    monkeypatch.setattr(MemoryDAO, "_search_v4_memory_uncached", concurrent_commit)
    assert await _search(cached) == baseline
    assert await _search(cached) == []


@pytest.mark.asyncio
async def test_cancelled_search_discards_partial_cache_and_releases_lock(
    scope_env, monkeypatch
):
    _sql, cached, uncached = scope_env
    original = MemoryDAO._search_v4_memory_uncached

    async def cancelled(self, **kwargs):
        await original(self, **kwargs)
        raise asyncio.CancelledError

    for jurisdiction in (None, "TR", "US", "DE"):
        await _search(cached, jurisdiction=jurisdiction)
    with monkeypatch.context() as patch:
        patch.setattr(MemoryDAO, "_search_v4_memory_uncached", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await _search(cached, jurisdiction="FR")
    async with cached._sql.search_connection() as db:
        async with db.execute(
            "SELECT name FROM sqlite_temp_master WHERE sql LIKE '%USING fts5%'"
        ) as cur:
            assert await cur.fetchall() == []
    assert not cached._search_scope_cache
    assert await _search(cached) == await _search(uncached)


async def _grow_search_store(sql, count):
    """Add realistic searchable rows directly, excluding setup from timings."""
    async with sql.transaction() as db:
        async with db.execute(
            "SELECT * FROM memory_mutations ORDER BY rowid LIMIT 1"
        ) as cur:
            mutation = dict(await cur.fetchone())
        async with db.execute(
            "SELECT * FROM v4_assertions ORDER BY rowid LIMIT 1"
        ) as cur:
            assertion = dict(await cur.fetchone())
        await db.execute(
            "INSERT INTO documents(document_id, tenant_id, dataset_id, title) VALUES (?, ?, ?, 'Benchmark')",
            (assertion["document_id"], assertion["tenant_id"], assertion["dataset_id"]),
        )
        await db.execute(
            "INSERT INTO document_revisions(revision_id, tenant_id, document_id, revision_number, content_hash) VALUES (?, ?, ?, 1, 'benchmark')",
            (
                assertion["revision_id"],
                assertion["tenant_id"],
                assertion["document_id"],
            ),
        )
        source_chunk = dict(
            chunk_id=assertion["chunk_id"],
            tenant_id=assertion["tenant_id"],
            dataset_id=assertion["dataset_id"],
            revision_id=assertion["revision_id"],
            ordinal=0,
            content_payload=mutation["content_payload"],
            content_hash="benchmark",
            source_ref=assertion["source_ref"],
        )
        async with db.execute(
            "SELECT chunk_id FROM v4_assertions ORDER BY rowid"
        ) as cur:
            seed_ids = [row[0] for row in await cur.fetchall()]
        for ordinal, chunk_id in enumerate(seed_ids):
            row = {**source_chunk, "chunk_id": chunk_id, "ordinal": ordinal}
            await db.execute(
                f"INSERT INTO source_chunks ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                tuple(row.values()),
            )
        # Exercise the real assertion FTS triggers, with one exact source chunk
        # per mutation and a shared in-scope legal entity.
        mutations, assertions, chunks = [], [], []
        for i in range(count):
            text = f"TBK m. {300 + i % 30} kira bedeli ödeme süresi kiracı temerrüt ihtar otuz gün. Article 12 privacy compliance officer. {i}"
            chunk = {
                **source_chunk,
                "chunk_id": f"bench-chunk-{i}",
                "ordinal": i + 100,
                "content_payload": text,
                "content_hash": f"bench-hash-{i}",
            }
            row = {
                **mutation,
                "mutation_id": f"bench-mut-{i}",
                "candidate_id": f"bench-candidate-{i}",
                "raw_log_id": None,
                "chunk_id": chunk["chunk_id"],
                "content_payload": text,
            }
            evidence = {
                **assertion,
                "assertion_id": f"bench-assertion-{i}",
                "mutation_id": row["mutation_id"],
                "chunk_id": chunk["chunk_id"],
                "evidence_span": text,
            }
            chunks.append(tuple(chunk.values()))
            mutations.append(tuple(row.values()))
            assertions.append(tuple(evidence.values()))
        for table, template, rows in [
            ("source_chunks", source_chunk, chunks),
            ("memory_mutations", mutation, mutations),
            ("v4_assertions", assertion, assertions),
        ]:
            columns = ",".join(template)
            placeholders = ",".join("?" for _ in template)
            await db.executemany(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", rows
            )
        await db.commit()


@pytest.mark.asyncio
async def test_3500_chunk_repeated_query_measurement(scope_env):
    import math
    import statistics
    import time

    sql, cached, uncached = scope_env
    await _grow_search_store(sql, 3498)
    timings = {}
    outputs = {}
    for name, dao in [("off", uncached), ("on", cached)]:
        # Warm connection/OS caches consistently; measure the next 20 queries.
        await _search(dao, QUERIES[0])
        durations, results = [], []
        for query in QUERIES[:20]:
            started = time.perf_counter()
            results.append(await _search(dao, query))
            durations.append(time.perf_counter() - started)
        timings[name] = durations
        outputs[name] = results
        print(
            f"3500 chunks cache={name}: p50={statistics.median(durations):.6f}s p95={sorted(durations)[math.ceil(0.95 * len(durations)) - 1]:.6f}s n={len(durations)}"
        )
    assert outputs["on"] == outputs["off"]


@pytest.mark.asyncio
async def test_active_vector_graph_and_query_options_remain_identical(scope_env):
    from tests.test_v4_retrieval_hardening_phase1 import TEST_EMBEDDING_IDENTITY

    _sql, cached, uncached = scope_env
    assertion = await _create_committed_mutation(
        cached,
        raw_log_id=3,
        tenant_id="t",
        dataset_id="d",
        agent_id="a",
        chunk_id="hybrid",
        content="TBK m. 314 kira bedeli",
        subject="Kiracı Ahmet",
        object_value="Mal sahibi Mehmet",
        evidence_span="TBK m. 314 kira bedeli ödeme süresi",
    )
    cached._vec.embedding_identity = TEST_EMBEDDING_IDENTITY
    cached._vec.search.return_value = [
        {"node_id": assertion["assertion_id"], "_distance": 0.042}
    ]
    cached._graph.search_v4_graph.return_value = [
        {
            "entity_id": assertion["object_entity_id"],
            "paths": [
                {
                    "assertion_ids": [assertion["assertion_id"]],
                    "entity_ids": [
                        assertion["subject_id"],
                        assertion["object_entity_id"],
                    ],
                    "seed_id": assertion["subject_id"],
                    "score": 0.8,
                }
            ],
        }
    ]
    for query in QUERIES[:5]:
        for options in [
            {},
            {
                "limit": 1,
                "rrf_k": 37,
                "rrf_weights": {
                    "bm25": 0.0,
                    "vector": 1.0,
                    "assertion": 1.0,
                    "graph": 2.0,
                },
            },
            {"graph_enabled": False},
        ]:
            on_meta, off_meta = {}, {}
            result = await _search(
                cached, query, certification_metadata=on_meta, **options
            )
            assert result == await _search(
                uncached, query, certification_metadata=off_meta, **options
            )
            assert on_meta == off_meta
            assert on_meta["vector_lane"]["search_executed"]
            if options.get("graph_enabled", True):
                assert any(
                    "graph" in row["retrieval_provenance"]["origins"] for row in result
                )
