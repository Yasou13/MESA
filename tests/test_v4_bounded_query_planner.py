"""Contracts for opt-in bounded adaptive V4 query retrieval."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.context_builder import ContextBuilder
from mesa_memory.retrieval.adaptive import (
    AdaptiveRetrievalResult,
    BoundedAdaptiveQueryRetriever,
    BoundedQueryPlanner,
    PlannerResult,
    fuse_query_results,
    normalize_planner_query,
    retrieval_is_weak,
)


def _candidate(
    candidate_id: str,
    *,
    origins: tuple[str, ...] = ("bm25",),
    evidence: str = "authoritative evidence",
) -> dict:
    return {
        "entity": {"canonical_name": candidate_id},
        "candidate_id": candidate_id,
        "evidence_id": candidate_id,
        "assertion_id": candidate_id,
        "source_chunk_id": f"chunk-{candidate_id}",
        "document_id": f"document-{candidate_id}",
        "rrf_score": 1.0 / 61.0,
        "final_score": 1.0 / 61.0,
        "provenance": [
            {
                "assertion_id": candidate_id,
                "subject_name": candidate_id,
                "predicate": "states",
                "literal_value": evidence,
                "evidence_span": evidence,
            }
        ],
        "retrieval_provenance": {
            "origins": list(origins),
            "lane_ranks": {origin: 1 for origin in origins},
            "raw_scores": {"bm25": 1.0},
        },
    }


class _Adapter:
    def __init__(self, response: object = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = 0
        self.prompts: list[str] = []

    async def acomplete(self, prompt: str, schema=None, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        if schema is not None and isinstance(self.response, dict):
            return schema.model_validate(self.response)
        return self.response


@pytest.mark.parametrize(
    ("response", "expected", "status"),
    [
        ({"queries": []}, (), "planner_no_valid_expansions"),
        (
            {"queries": ["zina nedeniyle boşanma süresi"]},
            ("zina nedeniyle boşanma süresi",),
            "planner_expanded",
        ),
        (
            {
                "queries": [
                    "zina nedeniyle boşanma süresi",
                    "zina öğrenildikten sonra dava süresi",
                ]
            },
            (
                "zina nedeniyle boşanma süresi",
                "zina öğrenildikten sonra dava süresi",
            ),
            "planner_expanded",
        ),
    ],
)
@pytest.mark.asyncio
async def test_planner_returns_zero_to_two_expansions(
    response: dict, expected: tuple[str, ...], status: str
) -> None:
    adapter = _Adapter(response)
    result = await BoundedQueryPlanner(adapter).plan("  Eşim beni aldattı  ")  # type: ignore[arg-type]

    assert result.expansions == expected
    assert result.status == status
    assert adapter.calls == 1
    assert "eşim beni aldattı" in adapter.prompts[0]
    assert "authoritative evidence" not in adapter.prompts[0]


@pytest.mark.parametrize(
    ("response", "status"),
    [
        (
            {"queries": ["bir", "iki", "üç"]},
            "planner_invalid_output_fallback_single",
        ),
        ("queries: [not structured]", "planner_invalid_output_fallback_single"),
        (None, "planner_invalid_output_fallback_single"),
    ],
)
@pytest.mark.asyncio
async def test_planner_rejects_invalid_structured_output(
    response: object, status: str
) -> None:
    result = await BoundedQueryPlanner(_Adapter(response)).plan("özgün sorgu")  # type: ignore[arg-type]

    assert result.expansions == ()
    assert result.status == status


@pytest.mark.parametrize(
    "error", [RuntimeError("provider down"), asyncio.TimeoutError()]
)
@pytest.mark.asyncio
async def test_planner_provider_failure_falls_back(error: Exception) -> None:
    result = await BoundedQueryPlanner(_Adapter(error=error)).plan("özgün sorgu")  # type: ignore[arg-type]

    assert result == PlannerResult(
        expansions=(), status="planner_unavailable_fallback_single"
    )


@pytest.mark.asyncio
async def test_planner_validation_is_deterministic_and_citation_safe() -> None:
    response = {
        "queries": [
            "  TMK m 161 zina nedeniyle boşanma  ",
            "4721 sayılı Türk Medeni Kanunu madde 161 dava süresi",
        ]
    }
    planner = BoundedQueryPlanner(_Adapter(response))  # type: ignore[arg-type]

    first = await planner.plan("TMK m.161 kapsamında boşanma")
    second = await planner.plan("TMK m.161 kapsamında boşanma")

    assert first == second
    assert first.status == "planner_expanded"
    assert first.expansions == (
        "tmk m 161 zina nedeniyle boşanma",
        "4721 sayılı türk medeni kanunu madde 161 dava süresi",
    )


@pytest.mark.parametrize(
    "queries",
    [
        ["özgün sorgu", "   "],
        ["özgün sorgu", "ÖZGÜN   SORGU"],
        ["TMK m.161 yerine TBK m.117"],
        ["TMK m.162 yeni madde"],
        ["satır\nsonu"],
        ["x" * 4097],
    ],
)
@pytest.mark.asyncio
async def test_planner_rejects_duplicate_blank_unsafe_or_overlong_expansions(
    queries: list[str],
) -> None:
    original = "TMK m.161 özgün sorgu" if "TMK" in queries[0] else "özgün sorgu"
    result = await BoundedQueryPlanner(_Adapter({"queries": queries})).plan(original)  # type: ignore[arg-type]

    assert result.expansions == ()
    assert result.status == "planner_no_valid_expansions"


def test_normalization_preserves_meaning_and_unicode() -> None:
    assert normalize_planner_query("  İŞ   KANUNU  ") == "iş kanunu"
    assert normalize_planner_query("TMK   m 161") == "tmk m 161"


def test_weak_trigger_is_pure_and_deterministic() -> None:
    strong = [_candidate("a", origins=("vector", "bm25"))]
    weak = [_candidate("a"), _candidate("b"), _candidate("c")]

    assert retrieval_is_weak(strong) is False
    assert retrieval_is_weak(weak) is True
    assert retrieval_is_weak([]) is True
    assert [retrieval_is_weak(deepcopy(weak)) for _ in range(3)] == [True] * 3


def test_query_fusion_deduplicates_and_preserves_inner_provenance() -> None:
    q0_shared = _candidate("shared", origins=("vector", "bm25"))
    q0_only = _candidate("q0-only")
    q1_shared = deepcopy(q0_shared)
    q1_rescue = _candidate("rescue")

    fused = fuse_query_results(
        [("Q0", [q0_shared, q0_only]), ("Q1", [q1_shared, q1_rescue])]
    )

    assert [item["candidate_id"] for item in fused].count("shared") == 1
    shared = next(item for item in fused if item["candidate_id"] == "shared")
    assert shared["retrieval_provenance"]["origins"] == ["vector", "bm25"]
    assert shared["retrieval_provenance"]["lane_ranks"] == {
        "vector": 1,
        "bm25": 1,
    }
    assert shared["retrieval_provenance"]["query_origins"] == [
        {"query_id": "Q0", "query_rank": 1},
        {"query_id": "Q1", "query_rank": 1},
    ]
    assert shared["query_fusion_score"] > 0
    assert any(item["candidate_id"] == "rescue" for item in fused)


def test_query_fusion_is_stable_and_original_has_more_authority() -> None:
    q0 = [_candidate("original")]
    q1 = [_candidate("noise-a"), _candidate("noise-b")]
    q2 = [_candidate("noise-a"), _candidate("noise-b")]

    first = fuse_query_results([("Q0", q0), ("Q1", q1), ("Q2", q2)])
    second = fuse_query_results([("Q0", q0), ("Q1", q1), ("Q2", q2)])

    assert first == second
    assert first[0]["candidate_id"] == "original"
    assert [item["candidate_id"] for item in first] == [
        "original",
        "noise-a",
        "noise-b",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("results", "expansions", "expected_dao_calls", "expected_planner_calls"),
    [
        (
            [[_candidate("strong", origins=("vector", "bm25"))]],
            ("unused",),
            1,
            0,
        ),
        ([[_candidate("weak")]], (), 1, 1),
        ([[_candidate("weak")], [_candidate("rescue")]], ("one",), 2, 1),
        (
            [
                [_candidate("weak")],
                [_candidate("rescue-one")],
                [_candidate("rescue-two")],
            ],
            ("one", "two"),
            3,
            1,
        ),
    ],
)
async def test_retrieval_and_planner_call_bounds(
    results: list[list[dict]],
    expansions: tuple[str, ...],
    expected_dao_calls: int,
    expected_planner_calls: int,
) -> None:
    dao = SimpleNamespace(search_v4_memory=AsyncMock(side_effect=results))
    planner = SimpleNamespace(
        plan=AsyncMock(
            return_value=PlannerResult(
                expansions=expansions,
                status=(
                    "planner_expanded" if expansions else "planner_no_valid_expansions"
                ),
            )
        )
    )
    retriever = BoundedAdaptiveQueryRetriever(dao, planner)  # type: ignore[arg-type]

    outcome = await retriever.retrieve(
        tenant_id="tenant",
        agent_id="agent",
        dataset_ids=["dataset"],
        query="original",
        limit=20,
        jurisdiction="TR",
        valid_at="2026-01-01T00:00:00+00:00",
        valid_from=None,
        valid_to=None,
        graph_enabled=False,
        request_principal_id="principal",
    )

    assert dao.search_v4_memory.await_count == expected_dao_calls
    assert planner.plan.await_count == expected_planner_calls
    assert outcome.diagnostics["retrieval_count"] == expected_dao_calls
    for call in dao.search_v4_memory.await_args_list:
        assert call.kwargs | {"query": "ignored"} == {
            "tenant_id": "tenant",
            "agent_id": "agent",
            "dataset_ids": ["dataset"],
            "query": "ignored",
            "limit": 20,
            "jurisdiction": "TR",
            "valid_at": "2026-01-01T00:00:00+00:00",
            "valid_from": None,
            "valid_to": None,
            "graph_enabled": False,
            "request_principal_id": "principal",
        }


@pytest.mark.asyncio
async def test_optional_expansion_failure_preserves_successful_results() -> None:
    dao = SimpleNamespace(
        search_v4_memory=AsyncMock(
            side_effect=[
                [_candidate("q0")],
                RuntimeError("optional backend failure"),
                [_candidate("q2")],
            ]
        )
    )
    planner = SimpleNamespace(
        plan=AsyncMock(
            return_value=PlannerResult(
                expansions=("one", "two"), status="planner_expanded"
            )
        )
    )

    outcome = await BoundedAdaptiveQueryRetriever(dao, planner).retrieve(  # type: ignore[arg-type]
        tenant_id="tenant",
        agent_id="agent",
        dataset_ids=["dataset"],
        query="original",
    )

    assert {item["candidate_id"] for item in outcome.candidates} == {"q0", "q2"}
    assert outcome.diagnostics["retrieval_count"] == 3
    assert outcome.diagnostics["expansion_failure_count"] == 1


@pytest.mark.asyncio
async def test_original_retrieval_failure_remains_fail_closed() -> None:
    dao = SimpleNamespace(
        search_v4_memory=AsyncMock(side_effect=RuntimeError("core failure"))
    )
    planner = SimpleNamespace(plan=AsyncMock())

    with pytest.raises(RuntimeError, match="core failure"):
        await BoundedAdaptiveQueryRetriever(dao, planner).retrieve(  # type: ignore[arg-type]
            tenant_id="tenant",
            agent_id="agent",
            dataset_ids=["dataset"],
            query="original",
        )
    planner.plan.assert_not_awaited()


@pytest.mark.asyncio
async def test_context_builder_single_mode_is_unchanged() -> None:
    candidate = _candidate("single")
    dao = SimpleNamespace(
        get_recent_logs=AsyncMock(return_value=[]),
        search_v4_memory=AsyncMock(return_value=[candidate]),
    )
    adaptive = SimpleNamespace(retrieve=AsyncMock())

    context = await ContextBuilder(dao, adaptive_retriever=adaptive).build_context(  # type: ignore[arg-type]
        tenant_id="tenant",
        agent_id="agent",
        dataset_ids=["dataset"],
        query="original",
    )

    dao.search_v4_memory.assert_awaited_once()
    adaptive.retrieve.assert_not_awaited()
    assert context["canonical_memories"][0]["candidate_id"] == "single"
    assert context["context_diagnostics"]["retrieval_mode"] == "single"


@pytest.mark.asyncio
async def test_context_builder_adaptive_mode_packs_one_deduplicated_list() -> None:
    shared = _candidate("shared", evidence="RESCUED_EVIDENCE")
    duplicate = deepcopy(shared)
    adaptive = SimpleNamespace(
        retrieve=AsyncMock(
            return_value=AdaptiveRetrievalResult(
                candidates=[shared, duplicate],
                diagnostics={
                    "retrieval_mode": "adaptive",
                    "planner_used": True,
                    "planner_status": "planner_expanded",
                    "expansion_count": 1,
                    "retrieval_count": 2,
                    "expansion_failure_count": 0,
                },
            )
        )
    )
    dao = SimpleNamespace(
        get_recent_logs=AsyncMock(return_value=[]), search_v4_memory=AsyncMock()
    )

    context = await ContextBuilder(dao, adaptive_retriever=adaptive).build_context(  # type: ignore[arg-type]
        tenant_id="tenant",
        agent_id="agent",
        dataset_ids=["dataset"],
        query="original",
        retrieval_mode="adaptive",
        token_budget=2048,
    )

    adaptive.retrieve.assert_awaited_once()
    dao.search_v4_memory.assert_not_awaited()
    assert len(context["canonical_memories"]) == 1
    assert "RESCUED_EVIDENCE" in context["formatted_context"]
    assert context["actual_token_count"] <= 2048
    assert context["context_diagnostics"]["retrieval_count"] == 2
