"""Deterministic and content-free V4 closure evidence contracts."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.capture_v4_closure_evidence import build_manifest, main


def _junit(path: Path) -> None:
    path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites name="private-suite">
  <testsuite name="must-not-leak" tests="3" failures="0" errors="0"
             skipped="1" time="1.25">
    <testcase name="private-content" />
    <system-out>exact private evidence payload</system-out>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )


def test_evidence_manifest_is_deterministic_bounded_and_content_free(
    tmp_path: Path,
) -> None:
    junit = tmp_path / "private-report.xml"
    _junit(junit)

    manifest = build_manifest([junit])

    assert manifest["kind"] == "mesa-v4-closure-evidence"
    assert manifest["schema_version"] == 1
    assert manifest["alembic_head"] == "b3c4d5e6f7a8"
    assert len(manifest["openapi"]["sha256"]) == 64
    assert manifest["openapi"]["path_count"] > 0
    assert manifest["openapi"]["operation_count"] >= manifest["openapi"]["path_count"]
    assert manifest["tests"]["available"] is True
    assert manifest["tests"]["passed"] is True
    assert manifest["tests"]["totals"] == {
        "tests": 3,
        "failures": 0,
        "errors": 0,
        "skipped": 1,
        "duration_seconds": 1.25,
    }
    assert manifest["rrf_ablation"] == {
        "sha256": "b17232b14a65571428664b8aa0d3b80b442a4bac0d9fa47b750ee176f80b0b97",
        "metric": "MRR",
        "lane_order": ["vector", "bm25", "assertion", "graph"],
        "query_count": 2,
        "lane_set_count": 4,
        "scores": {
            "rrf_all": 1.0,
            "vector_bm25": 0.75,
            "vector_graph": 1.0,
            "vector_only": 0.5,
        },
        "delta_vs_vector": {
            "rrf_all": 0.5,
            "vector_bm25": 0.25,
            "vector_graph": 0.5,
        },
    }
    assert manifest["fixture_store_counts"] == {
        "canonical_entities": 1,
        "canonical_assertions": 1,
        "fts_entities": 1,
        "vector_registry_artifacts": 1,
        "graph_registry_artifacts": 2,
        "active_source_owners": 3,
        "projection_generations": 1,
        "active_runtime_pointers": 1,
    }
    assert manifest["golden_ids"] == {
        "candidate_id": "cc753f19-ef38-5285-8d5e-ba615eb659db",
        "mutation_id": "d08303fc-b739-595b-97ea-617cfbfed1a1",
        "pipeline_run_id": "05b8c910-57a5-5e0d-b25f-b31c81c405ff",
        "entity_id": "7aa5b507-edac-58a1-813c-8300fd7dcb95",
        "assertion_id": "144221cb-88b6-5be9-a6e3-b218b87a5daa",
    }
    encoded = json.dumps(manifest, sort_keys=True)
    assert "exact private evidence payload" not in encoded
    assert "must-not-leak" not in encoded
    assert "private-report.xml" not in encoded
    assert str(tmp_path) not in encoded


def test_evidence_cli_records_missing_junit_without_exposing_its_path(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "secret-missing.xml"
    output = tmp_path / "evidence.json"

    assert main(["--junit", str(missing), "--output", str(output)]) == 0

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["tests"] == {
        "available": False,
        "passed": False,
        "reports": [{"available": False}],
        "totals": {
            "tests": 0,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "duration_seconds": 0.0,
        },
    }
    assert "secret-missing.xml" not in output.read_text(encoding="utf-8")


def test_ci_uploads_closure_evidence_from_v4_and_migration_jobs() -> None:
    workflow = (
        Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")

    assert workflow.count("scripts/capture_v4_closure_evidence.py") == 2
    assert "--junit v4-contract.xml" in workflow
    assert "--junit migration-dr.xml" in workflow
    assert "v4-closure-evidence.json" in workflow
    assert "migration-dr-evidence.json" in workflow
    assert "tests/test_rebuild_provider_rehearsal.py" in workflow
