"""Regression and contract tests for Profile A V4 Soak Test Runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_evals import soak_test
from mesa_evals.soak_test import (
    DeterministicSoakProvider,
    SoakEvaluationResult,
    SoakItem,
    SoakMetrics,
    _op_purge,
    _op_rollback,
    evaluate_profile_a_result,
    evaluate_resource_trends,
    format_final_report,
    get_process_resources,
    is_production_certification_duration,
    query_runtime_state,
)
from mesa_memory.extraction.service import (
    DeterministicFactValidator,
    FactExtractionResponse,
)


def test_deterministic_provider_extracts_non_empty_facts() -> None:
    provider = DeterministicSoakProvider()
    prompt = (
        "Instructions...\n"
        "<UNTRUSTED_SOURCE>\n"
        "SOAK-000123 is linked to CASE-000123. Additional Turkish legal text.\n"
        "</UNTRUSTED_SOURCE>\n"
    )
    result = provider.complete(prompt, schema=FactExtractionResponse)
    assert isinstance(result, FactExtractionResponse)
    assert len(result.facts) >= 1
    fact = result.facts[0]
    assert fact.subject == "SOAK-000123"
    assert fact.predicate == "LINKED_TO"
    assert fact.object == "CASE-000123"
    assert fact.confidence == 1.0
    assert "SOAK-000123 is linked to CASE-000123" in (fact.source_span or "")

    source_text = "SOAK-000123 is linked to CASE-000123. Additional Turkish legal text."
    assert DeterministicFactValidator.validate(fact, source_text=source_text) is True


def test_deterministic_provider_revision_extraction() -> None:
    provider = DeterministicSoakProvider()
    prompt = (
        "<UNTRUSTED_SOURCE>\n"
        "SOAK-000042 is updated to STATUS-RESOLVED-000042.\n"
        "</UNTRUSTED_SOURCE>\n"
    )
    result = provider.complete(prompt, schema=FactExtractionResponse)
    assert isinstance(result, FactExtractionResponse)
    fact = result.facts[0]
    assert fact.subject == "SOAK-000042"
    assert fact.predicate == "UPDATED_TO"
    assert fact.object == "STATUS-RESOLVED-000042"


def test_deterministic_provider_normalized_embedding() -> None:
    provider = DeterministicSoakProvider()
    vec = provider.embed("SOAK-000123 test query")
    assert len(vec) == 384
    norm = sum(x * x for x in vec) ** 0.5
    assert abs(norm - 1.0) < 1e-4


def test_certification_duration_boundaries() -> None:
    assert is_production_certification_duration(300) is False
    assert is_production_certification_duration(1800) is False
    assert is_production_certification_duration(86_399) is False
    assert is_production_certification_duration(86_400) is True
    assert is_production_certification_duration(100_000) is True


def _make_clean_eval_kwargs(
    actual_elapsed: float = 300.0, requested_duration: float = 300.0
) -> dict[str, Any]:
    return {
        "status": "ok",
        "actual_elapsed_s": actual_elapsed,
        "requested_duration_s": requested_duration,
        "total_operations": 50,
        "successful_operations": 50,
        "failed_operations": 0,
        "counts": {
            "insert": 15,
            "search": 15,
            "revision": 5,
            "idempotent_insert": 4,
            "rollback": 3,
            "purge": 3,
            "context": 3,
            "cross_scope": 2,
        },
        "correctness": {
            "wrong_retrieval": 0,
            "missing_committed_memory": 0,
            "deleted_memory_visible": 0,
            "cross_scope_results": 0,
            "consistency_failures": 0,
        },
        "final_health": "healthy",
        "health_checks_total": 10,
        "health_checks_failed": 0,
        "max_consecutive_health_failures": 0,
        "resource_eval": {
            "evaluated": True,
            "possible_memory_leak": False,
            "thread_leak_detected": False,
            "fd_leak_detected": False,
        },
        "drain_completed": True,
        "remaining_backlog": 0,
        "runtime_state_measurable": True,
        "runtime": {
            "pending_mutations": 0,
            "failed_mutations": 0,
            "projection_backlog": 0,
            "dead_letters": 0,
        },
    }


def test_evaluate_profile_a_result_smoke_pass() -> None:
    kwargs = _make_clean_eval_kwargs(actual_elapsed=300.0, requested_duration=300.0)
    res = evaluate_profile_a_result(**kwargs)
    assert res.verdict == "SMOKE PASS — NOT CERTIFICATION"
    assert res.exit_code == 0
    assert res.is_certification_eligible is False
    assert len(res.reasons) == 0


def test_evaluate_profile_a_result_certification_pass() -> None:
    kwargs = _make_clean_eval_kwargs(
        actual_elapsed=86_400.0, requested_duration=86_400.0
    )
    res = evaluate_profile_a_result(**kwargs)
    assert res.verdict == "PROFILE A PASS"
    assert res.exit_code == 0
    assert res.is_certification_eligible is True
    assert len(res.reasons) == 0


def test_evaluate_profile_a_result_short_elapsed_cannot_certify() -> None:
    kwargs = _make_clean_eval_kwargs(actual_elapsed=100.0, requested_duration=86_400.0)
    res = evaluate_profile_a_result(**kwargs)
    assert res.is_certification_eligible is False
    assert res.verdict == "SMOKE PASS — NOT CERTIFICATION"


def test_evaluate_profile_a_result_preflight_fail_exit_code() -> None:
    kwargs = _make_clean_eval_kwargs()
    kwargs["status"] = "pre_flight_failed"
    res = evaluate_profile_a_result(**kwargs)
    assert res.verdict == "PRE-FLIGHT FAILED"
    assert res.exit_code == 1


def test_evaluate_profile_a_result_interrupted_exit_code() -> None:
    kwargs = _make_clean_eval_kwargs()
    kwargs["status"] = "interrupted"
    res = evaluate_profile_a_result(**kwargs)
    assert res.verdict == "INTERRUPTED — NOT CERTIFIED"
    assert res.exit_code == 130


def test_evaluate_profile_a_result_final_health_unhealthy_fails() -> None:
    kwargs = _make_clean_eval_kwargs(
        actual_elapsed=86_400.0, requested_duration=86_400.0
    )
    kwargs["final_health"] = "degraded"
    res = evaluate_profile_a_result(**kwargs)
    assert res.verdict == "PROFILE A FAIL"
    assert res.exit_code == 1
    assert any("Final runtime health is not healthy" in r for r in res.reasons)


def test_evaluate_profile_a_result_consecutive_health_failures_fails() -> None:
    kwargs = _make_clean_eval_kwargs(
        actual_elapsed=86_400.0, requested_duration=86_400.0
    )
    kwargs["max_consecutive_health_failures"] = 4
    res = evaluate_profile_a_result(**kwargs)
    assert res.verdict == "PROFILE A FAIL"
    assert res.exit_code == 1
    assert any(
        "Exceeded max consecutive health check failures" in r for r in res.reasons
    )


def test_evaluate_profile_a_result_cross_scope_leak_fails() -> None:
    kwargs = _make_clean_eval_kwargs()
    kwargs["correctness"]["cross_scope_results"] = 1
    res = evaluate_profile_a_result(**kwargs)
    assert res.verdict == "SMOKE FAIL"
    assert res.exit_code == 1
    assert any("cross_scope_results=1" in r for r in res.reasons)


def test_evaluate_profile_a_result_missing_lifecycle_op_fails_certification() -> None:
    kwargs = _make_clean_eval_kwargs(
        actual_elapsed=86_400.0, requested_duration=86_400.0
    )
    kwargs["counts"]["purge"] = 0
    res = evaluate_profile_a_result(**kwargs)
    assert res.verdict == "PROFILE A FAIL"
    assert res.exit_code == 1
    assert any(
        "Required lifecycle operation had 0 executions: purge" in r for r in res.reasons
    )


def test_evaluate_profile_a_result_drain_incomplete_fails() -> None:
    kwargs = _make_clean_eval_kwargs(
        actual_elapsed=86_400.0, requested_duration=86_400.0
    )
    kwargs["drain_completed"] = False
    kwargs["remaining_backlog"] = 5
    res = evaluate_profile_a_result(**kwargs)
    assert res.verdict == "PROFILE A FAIL"
    assert res.exit_code == 1
    assert any("Drain phase did not complete cleanly" in r for r in res.reasons)


def test_evaluate_profile_a_result_resource_leaks_fail() -> None:
    kwargs = _make_clean_eval_kwargs(
        actual_elapsed=86_400.0, requested_duration=86_400.0
    )
    kwargs["resource_eval"]["possible_memory_leak"] = True
    res = evaluate_profile_a_result(**kwargs)
    assert res.verdict == "PROFILE A FAIL"
    assert res.exit_code == 1
    assert any("possible memory leak" in r for r in res.reasons)


def test_evaluate_profile_a_result_unmeasured_runtime_state_fails_certification() -> (
    None
):
    kwargs = _make_clean_eval_kwargs(
        actual_elapsed=86_400.0, requested_duration=86_400.0
    )
    kwargs["runtime_state_measurable"] = False
    res = evaluate_profile_a_result(**kwargs)
    assert res.verdict == "PROFILE A FAIL"
    assert res.exit_code == 1
    assert any(
        "Runtime state queue/backlog metrics could not be verified" in r
        for r in res.reasons
    )


def test_evaluate_profile_a_result_unevaluated_resources_fails_certification() -> None:
    kwargs = _make_clean_eval_kwargs(
        actual_elapsed=86_400.0, requested_duration=86_400.0
    )
    kwargs["resource_eval"]["evaluated"] = False
    res = evaluate_profile_a_result(**kwargs)
    assert res.verdict == "PROFILE A FAIL"
    assert res.exit_code == 1
    assert any("Resource telemetry could not be evaluated" in r for r in res.reasons)


def test_evaluate_profile_a_result_failed_mutations_fails_certification() -> None:
    kwargs = _make_clean_eval_kwargs(
        actual_elapsed=86_400.0, requested_duration=86_400.0
    )
    kwargs["runtime"]["failed_mutations"] = 2
    res = evaluate_profile_a_result(**kwargs)
    assert res.verdict == "PROFILE A FAIL"
    assert res.exit_code == 1
    assert any("failed_mutations=2" in r for r in res.reasons)


def test_format_final_report_handles_unavailable_metrics_cleanly() -> None:
    snap: dict[str, Any] = {
        "total_operations": 50,
        "successful_operations": 50,
        "failed_operations": 0,
        "counts": {
            "insert": 20,
            "search": 20,
            "revision": 5,
            "idempotent_insert": 2,
            "rollback": 1,
            "purge": 1,
            "context": 1,
            "cross_scope": 1,
        },
        "correctness": {
            "wrong_retrieval": 0,
            "missing_committed_memory": 0,
            "deleted_memory_visible": 0,
            "cross_scope_results": 0,
            "consistency_failures": 0,
        },
        "runtime": {
            "pending_mutations": None,
            "failed_mutations": None,
            "projection_backlog": None,
            "dead_letters": None,
        },
        "health": {
            "health_checks_total": 5,
            "health_checks_failed": 0,
            "consecutive_health_failures": 0,
            "max_consecutive_health_failures": 0,
            "final_health": "healthy",
        },
    }
    eval_res = SoakEvaluationResult(
        verdict="SMOKE PASS — NOT CERTIFICATION",
        exit_code=0,
        is_certification_eligible=False,
        reasons=[],
        critical_failures={},
    )
    res_unavail = {
        "status": "unavailable",
        "rss_mb": None,
        "threads": None,
        "open_fds": None,
    }
    eval_unavail = {"evaluated": False}

    report = format_final_report(
        snap,
        eval_res,
        mode_name="Remote Server (http://prod:8000)",
        actual_elapsed_s=300.0,
        requested_duration_s=300.0,
        resource_eval=eval_unavail,
        latest_resources=res_unavail,
        start_resources=res_unavail,
        peak_rss_mb=None,
        drain_completed=True,
        remaining_backlog=0,
        runtime_state_measurable=False,
    )

    assert "Pending mutations: unavailable" in report
    assert "Failed mutations: unavailable" in report
    assert "Projection backlog: unavailable" in report
    assert "Dead letters: unavailable" in report
    assert "Runtime state measurable: False" in report
    assert "Start RSS: unavailable" in report
    assert "Final RSS: unavailable" in report
    assert "Peak RSS: unavailable" in report
    assert "Memory leak suspicion: Inconclusive" in report
    assert "SMOKE PASS — NOT CERTIFICATION" in report


@pytest.mark.asyncio
async def test_metrics_accumulator_and_snapshot_serialization() -> None:
    metrics = SoakMetrics()
    await metrics.record_op("insert", success=True, latency_ms=15.0, status_code=202)
    await metrics.record_op("search", success=True, latency_ms=5.0, status_code=200)
    await metrics.record_op(
        "cross_scope", success=True, latency_ms=8.0, status_code=200
    )
    await metrics.record_commit(120.0)
    await metrics.record_health_check(True, "healthy")
    await metrics.record_health_check(False, "500")
    await metrics.record_health_check(False, "500")
    assert metrics.consecutive_health_failures == 2
    assert metrics.max_consecutive_health_failures == 2
    # Recovery resets consecutive but retains peak in max_consecutive_health_failures
    await metrics.record_health_check(True, "healthy")
    assert metrics.consecutive_health_failures == 0
    assert metrics.max_consecutive_health_failures == 2

    await metrics.record_correctness_violation("wrong_retrieval")

    snap = await metrics.snapshot()
    serialized = json.dumps(snap, ensure_ascii=False)
    deserialized = json.loads(serialized)

    assert deserialized["total_operations"] == 3
    assert deserialized["counts"]["insert"] == 1
    assert deserialized["counts"]["search"] == 1
    assert deserialized["counts"]["cross_scope"] == 1
    assert deserialized["health"]["max_consecutive_health_failures"] == 2
    assert deserialized["latencies"]["insert"]["p50_ms"] == 15.0
    assert deserialized["latencies"]["commit"]["p50_ms"] == 120.0
    assert deserialized["correctness"]["wrong_retrieval"] == 1
    assert deserialized["runtime"]["pending_mutations"] is None
    assert deserialized["runtime"]["projection_backlog"] is None


@pytest.mark.asyncio
async def test_query_runtime_state_from_mocked_dao() -> None:
    mock_dao = MagicMock()
    mock_cursor_mut = AsyncMock()
    mock_cursor_mut.fetchall.return_value = [
        ("COMMITTED", 10),
        ("PENDING", 2),
        ("FAILED", 1),
    ]
    mock_cursor_outbox = AsyncMock()
    mock_cursor_outbox.fetchall.return_value = [
        ("PENDING", 3),
        ("IN_FLIGHT", 1),
        ("DEAD_LETTER", 0),
    ]

    mock_conn = MagicMock()
    mock_conn.execute.side_effect = [
        AsyncMock(
            __aenter__=AsyncMock(return_value=mock_cursor_mut),
            __aexit__=AsyncMock(return_value=None),
        ),
        AsyncMock(
            __aenter__=AsyncMock(return_value=mock_cursor_outbox),
            __aexit__=AsyncMock(return_value=None),
        ),
    ]

    mock_sql = MagicMock()
    mock_sql.connection.return_value = AsyncMock(
        __aenter__=AsyncMock(return_value=mock_conn),
        __aexit__=AsyncMock(return_value=None),
    )
    mock_dao._sql = mock_sql

    mock_client = AsyncMock()
    state = await query_runtime_state(mock_client, mock_dao)

    assert state["pending_mutations"] == 2
    assert state["failed_mutations"] == 1
    assert state["projection_backlog"] == 4
    assert state["dead_letters"] == 0


def test_process_resource_monitoring_and_leak_eval() -> None:
    resources = get_process_resources(is_embedded=True)
    assert resources["status"] in {"available", "partial_psutil_missing"}
    assert resources["rss_mb"] is not None and resources["rss_mb"] > 0

    remote_res = get_process_resources(pid=None, is_embedded=False)
    assert remote_res["status"] == "unavailable"
    assert remote_res["rss_mb"] is None

    leak_samples = [
        {"status": "available", "rss_mb": 100.0, "open_fds": 20, "threads": 5},
        {"status": "available", "rss_mb": 130.0, "open_fds": 22, "threads": 5},
        {"status": "available", "rss_mb": 170.0, "open_fds": 24, "threads": 6},
        {"status": "available", "rss_mb": 220.0, "open_fds": 25, "threads": 6},
        {"status": "available", "rss_mb": 280.0, "open_fds": 25, "threads": 6},
    ]
    eval_leak = evaluate_resource_trends(leak_samples)
    assert eval_leak["evaluated"] is True
    assert eval_leak["possible_memory_leak"] is True


@pytest.mark.asyncio
async def test_rollback_candidate_selection_and_state_transition() -> None:
    mock_client = AsyncMock()
    mock_post_resp = MagicMock(status_code=202)
    mock_get_resp = MagicMock(status_code=200, json=lambda: {"state": "ROLLED_BACK"})
    mock_client.post.return_value = mock_post_resp
    mock_client.get.return_value = mock_get_resp

    metrics = SoakMetrics()
    item1 = SoakItem(
        seq=1,
        doc_id="doc-1",
        rev_id="rev-1-2",
        chunk_id="chk-1",
        subj="SOAK-1",
        obj="OBJ-1",
        content="...",
        mutation_id="mut-1",
        idempotency_key="idemp-1",
        dataset_id="ds",
        session_id="sess",
        revision_number=2,
        state="COMMITTED",
    )
    active_items = [item1]

    # First rollback succeeds
    await _op_rollback(mock_client, metrics=metrics, active_items=active_items)
    assert item1.state == "ROLLED_BACK"
    assert metrics.rollback_count == 1

    # Second rollback attempts on active_items should find no candidate because state is ROLLED_BACK
    await _op_rollback(mock_client, metrics=metrics, active_items=active_items)
    assert metrics.rollback_count == 1  # unchanged


@pytest.mark.asyncio
async def test_purge_retries_and_verifies_absence() -> None:
    mock_client = AsyncMock()
    del_resp = MagicMock(status_code=202)
    mock_client.delete.return_value = del_resp

    # First search returns active item, second search returns empty (purged)
    search_resp_present = MagicMock(
        status_code=200,
        json=lambda: {"results": [{"entity": {"canonical_name": "SOAK-1"}}]},
    )
    search_resp_empty = MagicMock(status_code=200, json=lambda: {"results": []})
    mock_client.post.side_effect = [search_resp_present, search_resp_empty]

    metrics = SoakMetrics()
    items = [
        SoakItem(
            seq=i,
            doc_id=f"doc-{i}",
            rev_id="rev-1",
            chunk_id="chk-1",
            subj=f"SOAK-{i}",
            obj="OBJ",
            content="...",
            mutation_id=f"mut-{i}",
            idempotency_key="idemp",
            dataset_id="ds",
            session_id="sess",
            state="COMMITTED",
        )
        for i in range(5)
    ]

    await _op_purge(
        mock_client,
        session_id="sess",
        tenant_id="t",
        workspace_id="w",
        dataset_id="ds",
        metrics=metrics,
        active_items=items,
    )

    assert metrics.purge_count == 1
    assert metrics.deleted_memory_visible_count == 0


@pytest.mark.asyncio
async def test_embedded_soak_run_integration(tmp_path: Path) -> None:
    """Run a fast 5-second in-process soak test exercising real V4 lifecycle."""
    log_file = tmp_path / "soak_test_fast.jsonl"
    storage_root = tmp_path / "storage"

    report = await soak_test.run_soak(
        base_url="http://localhost:8000",
        api_key="fast-test-key",
        duration=5.0,
        rps=2.0,
        telemetry_interval=1.0,
        drain_timeout=5.0,
        log_file=log_file,
        embedded=True,
        storage_root=storage_root,
    )

    assert report["total_operations"] >= 2
    assert report["successful_operations"] > 0
    assert report["correctness"]["missing_committed_memory"] == 0
    assert report["correctness"]["wrong_retrieval"] == 0
    assert report["correctness"]["cross_scope_results"] == 0
    assert report["production_certification_duration_valid"] is False
    assert report["evaluation"]["verdict"] == "SMOKE PASS — NOT CERTIFICATION"
    assert report["evaluation"]["exit_code"] == 0
    assert report["drain_completed"] is True
    assert report["runtime_state_measurable"] is True
    assert "SMOKE PASS — NOT CERTIFICATION" in report["report_text"]
    assert log_file.exists()

    with open(log_file, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) >= 1
    assert any(line.get("_type") == "telemetry_tick" for line in lines)
    tick_sample = next(line for line in lines if line.get("_type") == "telemetry_tick")
    assert "health" in tick_sample
    assert "max_consecutive_health_failures" in tick_sample
    assert "runtime" in tick_sample
    assert "resources" in tick_sample
