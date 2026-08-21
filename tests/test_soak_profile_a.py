"""Regression and contract tests for Profile A V4 Soak Test Runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mesa_evals import soak_test
from mesa_evals.soak_test import (
    DeterministicSoakProvider,
    SoakMetrics,
    evaluate_resource_trends,
    format_final_report,
    get_process_resources,
    is_production_certification_duration,
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


def test_final_report_smoke_vs_certification_verdict() -> None:
    # 5 min run (300s) with 0 errors -> SMOKE PASS — NOT CERTIFICATION
    snap_ok: dict[str, Any] = {
        "total_operations": 50,
        "successful_operations": 50,
        "failed_operations": 0,
        "counts": {"insert": 20, "search": 20, "revision": 5, "rollback": 2, "purge": 3},
        "correctness": {
            "wrong_retrieval": 0,
            "missing_committed_memory": 0,
            "deleted_memory_visible": 0,
            "cross_scope_results": 0,
            "consistency_failures": 0,
        },
        "runtime": {"pending_mutations": 0, "failed_mutations": 0, "dead_letters": 0},
    }
    eval_ok = {"possible_memory_leak": False, "thread_leak_detected": False, "fd_leak_detected": False}
    res = {"rss_mb": 150.0, "cpu_percent": 1.0, "threads": 8, "open_fds": 30}

    smoke_report = format_final_report(snap_ok, 300, eval_ok, res, res, 160.0)
    assert "RESULT:\nSMOKE PASS — NOT CERTIFICATION" in smoke_report
    assert "Certification duration valid: False" in smoke_report

    # 24 hour run (86400s) with 0 errors -> PROFILE A PASS
    cert_report = format_final_report(snap_ok, 86_400, eval_ok, res, res, 180.0)
    assert "RESULT:\nPROFILE A PASS" in cert_report
    assert "Certification duration valid: True" in cert_report


def test_final_report_critical_correctness_failure_fails_test() -> None:
    # Any wrong retrieval fails both smoke and certification
    snap_fail: dict[str, Any] = {
        "total_operations": 50,
        "successful_operations": 49,
        "failed_operations": 1,
        "counts": {"insert": 20, "search": 20, "revision": 5, "rollback": 2, "purge": 3},
        "correctness": {
            "wrong_retrieval": 1,
            "missing_committed_memory": 0,
            "deleted_memory_visible": 0,
            "cross_scope_results": 0,
            "consistency_failures": 0,
        },
        "runtime": {"pending_mutations": 0, "failed_mutations": 0, "dead_letters": 0},
    }
    eval_ok = {"possible_memory_leak": False, "thread_leak_detected": False, "fd_leak_detected": False}
    res = {"rss_mb": 150.0, "cpu_percent": 1.0, "threads": 8, "open_fds": 30}

    smoke_fail = format_final_report(snap_fail, 300, eval_ok, res, res, 160.0)
    assert "RESULT:\nSMOKE FAIL" in smoke_fail

    cert_fail = format_final_report(snap_fail, 86_400, eval_ok, res, res, 180.0)
    assert "RESULT:\nPROFILE A FAIL" in cert_fail


@pytest.mark.asyncio
async def test_metrics_accumulator_and_snapshot_serialization() -> None:
    metrics = SoakMetrics()
    await metrics.record_op("insert", success=True, latency_ms=15.0, status_code=202)
    await metrics.record_op("search", success=True, latency_ms=5.0, status_code=200)
    await metrics.record_commit(120.0)
    await metrics.record_correctness_violation("wrong_retrieval")

    snap = await metrics.snapshot()
    serialized = json.dumps(snap, ensure_ascii=False)
    deserialized = json.loads(serialized)

    assert deserialized["total_operations"] == 2
    assert deserialized["counts"]["insert"] == 1
    assert deserialized["counts"]["search"] == 1
    assert deserialized["latencies"]["insert"]["p50_ms"] == 15.0
    assert deserialized["latencies"]["commit"]["p50_ms"] == 120.0
    assert deserialized["correctness"]["wrong_retrieval"] == 1


def test_process_resource_monitoring_and_leak_eval() -> None:
    resources = get_process_resources()
    assert resources["status"] in {"available", "partial_psutil_missing"}
    assert resources["rss_mb"] > 0

    # Test leak evaluator with steady monotonic growth
    leak_samples = [
        {"status": "available", "rss_mb": 100.0, "open_fds": 20, "threads": 5},
        {"status": "available", "rss_mb": 130.0, "open_fds": 22, "threads": 5},
        {"status": "available", "rss_mb": 170.0, "open_fds": 24, "threads": 6},
        {"status": "available", "rss_mb": 220.0, "open_fds": 25, "threads": 6},
    ]
    eval_leak = evaluate_resource_trends(leak_samples)
    assert eval_leak["possible_memory_leak"] is True


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
        concurrency=5,
        telemetry_interval=1.0,
        log_file=log_file,
        embedded=True,
        storage_root=storage_root,
    )

    assert report["total_operations"] >= 2
    assert report["successful_operations"] > 0
    assert report["correctness"]["missing_committed_memory"] == 0
    assert report["correctness"]["wrong_retrieval"] == 0
    assert report["production_certification_duration_valid"] is False
    assert "SMOKE PASS — NOT CERTIFICATION" in report["report_text"]
    assert log_file.exists()
