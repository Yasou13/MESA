"""Independent production-path proofs for evidence fusion and scope isolation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_v4_retrieval_hardening_phase1 import (
    TEST_EMBEDDING_IDENTITY,
    _create_committed_mutation,
)

from mesa_memory.context_builder import ContextBuilder
from mesa_storage.dao import MemoryDAO
from mesa_storage.retrieval_scope import rrf_fuse_lanes
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


async def make_env(tmp_path):
    sql = AsyncEngine(str(tmp_path / "audit.sqlite"))
    await sql.initialize()
    await initialize_schema(sql)
    vector = SimpleNamespace(
        embedding_identity=TEST_EMBEDDING_IDENTITY,
        compute_embedding=AsyncMock(return_value=[1.0, 0.0]),
        compute_query_embedding=AsyncMock(return_value=[1.0, 0.0]),
        upsert=AsyncMock(),
        search=AsyncMock(return_value=[]),
    )
    return sql, vector, MemoryDAO(sql, vector)


async def add(dao, n, subject, target, *, evidence="", **kwargs):
    return await _create_committed_mutation(
        dao,
        raw_log_id=n,
        tenant_id="tenant",
        agent_id="agent",
        dataset_id="dataset",
        chunk_id=f"chunk-{n}",
        document_id=f"doc-{n}",
        content=evidence or f"{subject} knows {target}",
        subject=subject,
        predicate="knows",
        object_value=target,
        evidence_span=evidence,
        **kwargs,
    )


async def search(dao, query="Alice"):
    return await dao.search_v4_memory(
        tenant_id="tenant",
        agent_id="agent",
        dataset_ids=["dataset"],
        query=query,
        limit=20,
    )


def test_rrf_duplicate_lane_hits_do_not_vote_twice():
    expected = rrf_fuse_lanes({"vector": ["a", "b"], "bm25": ["a"]})
    assert rrf_fuse_lanes({"bm25": ["a"], "vector": ["a", "a", "b"]}) == expected


@pytest.mark.asyncio
async def test_real_three_lanes_fuse_once_and_repeat_deterministically(tmp_path):
    sql, vector, dao = await make_env(tmp_path)
    try:
        fact = await add(dao, 1, "Alice", "Aurora", evidence="Alice knows Aurora")
        aid = fact["assertion_id"]
        vector.search.return_value = [{"node_id": aid, "_distance": 0.125}] * 2
        first = await search(dao)
        assert len(first) == 1
        assert first[0]["candidate_id"] == first[0]["evidence_id"] == aid
        assert first[0]["retrieval_provenance"]["lane_ranks"] == {
            "vector": 1,
            "bm25": 1,
            "assertion": 1,
        }
        assert first[0]["rrf_score"] == pytest.approx(3 / 61)
        for _ in range(3):
            assert await search(dao) == first
    finally:
        await sql.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,evidence,expected",
    [
        ("Anayasa m.117 onay", "onay 117 işlem", 1.0),
        ("TMK m.117", "CMK m.117", 0.25),
        ("AY 36", "AY 36", 1.5),
        ("TBK m.117", "TBK m.118 ve CMK m.117", 1.25),
    ],
)
async def test_legal_reranking_uses_citation_identity(
    tmp_path, query, evidence, expected
):
    sql, vector, dao = await make_env(tmp_path)
    try:
        fact = await add(dao, 1, "Kural", "Sonuç", evidence=evidence)
        vector.search.return_value = [
            {"node_id": fact["assertion_id"], "_distance": 0.1}
        ]
        hit = (await search(dao, query))[0]
        assert hit["legal_factor"] == expected
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_all_lanes_reject_shared_entity_scope_collisions_before_fusion(tmp_path):
    sql, vector, dao = await make_env(tmp_path)
    try:
        cases = [
            ("valid", {}),
            ("open", {}),
            ("agent", {"agent_id": "other-agent"}),
            ("tenant", {"tenant_id": "other-tenant"}),
            ("dataset", {"dataset_id": "other-dataset"}),
            ("jurisdiction", {}),
            ("expired", {}),
            ("future", {}),
            ("deleted", {}),
        ]
        facts = {}
        for index, (name, overrides) in enumerate(cases, 1):
            args = dict(
                raw_log_id=index,
                tenant_id="tenant",
                agent_id="agent",
                dataset_id="dataset",
                chunk_id=f"chunk-{name}",
                content=f"Shared policy {name}",
                subject="Shared",
                predicate="policy",
                object_value="Destination",
                evidence_span=f"Shared policy {name}",
            )
            args.update(overrides)
            facts[name] = await _create_committed_mutation(dao, **args)
        async with sql.transaction() as db:
            for name, fact in facts.items():
                await db.execute(
                    "UPDATE v4_assertions SET jurisdiction=?, valid_from=?, valid_to=?, status=? WHERE assertion_id=?",
                    (
                        "DE" if name == "jurisdiction" else "TR",
                        (
                            "2027-01-01"
                            if name == "future"
                            else ("2025-01-01" if name == "valid" else "")
                        ),
                        (
                            "2025-01-01"
                            if name == "expired"
                            else ("2027-01-01" if name == "valid" else "")
                        ),
                        "DELETED" if name == "deleted" else "ACTIVE",
                        fact["assertion_id"],
                    ),
                )
            await db.commit()
        allowed = {facts[name]["assertion_id"] for name in ("valid", "open")}
        vector.search.return_value = [
            dict(node_id=f["assertion_id"], _distance=0.1) for f in facts.values()
        ]
        result = await dao.search_v4_memory(
            tenant_id="tenant",
            agent_id="agent",
            dataset_ids=["dataset"],
            query="Shared policy",
            jurisdiction="TR",
            valid_at="2026-09-26",
            limit=20,
        )
        assert vector.search.call_args.kwargs["allowed_node_ids"] == allowed
        assert {r["evidence_id"] for r in result} == allowed
        for hit in result:
            assert set(hit["retrieval_provenance"]["lane_ranks"]) == {
                "vector",
                "bm25",
                "assertion",
            }
            assert {p["assertion_id"] for p in hit["provenance"]} <= allowed
        ctx = await ContextBuilder(dao).build_context(
            tenant_id="tenant",
            agent_id="agent",
            dataset_ids=["dataset"],
            query="Shared policy",
            jurisdiction="TR",
            valid_at="2026-09-26",
            token_budget=2000,
        )
        assert {r["evidence_id"] for r in ctx["canonical_memories"]} == allowed
        for name, _ in cases[2:]:
            assert f"Shared policy {name}" not in ctx["formatted_context"]
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_oversized_top_evidence_cannot_discard_fitting_lower_evidence():
    dao = AsyncMock()
    dao.search_v4_memory.return_value = [
        {
            "entity": {"canonical_name": "Huge"},
            "provenance": [{"predicate": "rule", "literal_value": "word " * 2000}],
        },
        {
            "entity": {"canonical_name": "Useful"},
            "provenance": [{"predicate": "rule", "literal_value": "small fact"}],
        },
    ]
    result = await ContextBuilder(dao).build_context(
        tenant_id="tenant",
        agent_id="agent",
        dataset_ids=["dataset"],
        query="rule",
        token_budget=160,
    )
    assert result["actual_token_count"] <= 160
    assert "Useful" in result["formatted_context"]
    assert "Huge" not in result["formatted_context"]


@pytest.mark.asyncio
async def test_temporal_eligibility_compares_instants_with_offsets(tmp_path):
    sql, vector, dao = await make_env(tmp_path)
    try:
        fact = await add(dao, 1, "Alice", "Aurora")
        async with sql.transaction() as db:
            await db.execute(
                "UPDATE v4_assertions SET valid_from=?, valid_to=? WHERE assertion_id=?",
                (
                    "2026-09-26T12:00:00+03:00",
                    "2026-09-26T14:00:00+03:00",
                    fact["assertion_id"],
                ),
            )
            await db.commit()
        result = await dao.search_v4_memory(
            tenant_id="tenant",
            agent_id="agent",
            dataset_ids=["dataset"],
            query="Alice",
            valid_at="2026-09-26T10:00:00+00:00",
        )
        assert [r["assertion_id"] for r in result] == [fact["assertion_id"]]
    finally:
        await sql.close()
