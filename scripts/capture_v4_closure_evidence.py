"""Build a deterministic, content-free V4 closure evidence manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from mesa_evals.v4_rrf_ablation import evaluate_lane_ablation, fixed_legal_corpus
from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import MemoryDAO
from mesa_storage.retrieval_scope import V4_RRF_LANE_ORDER

_ROOT = Path(__file__).resolve().parents[1]
_ALEMBIC_INI = _ROOT / "mesa_storage" / "alembic.ini"
_MAX_JUNIT_BYTES = 16 * 1024 * 1024


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _alembic_config(database: Path | None = None) -> Config:
    config = Config(str(_ALEMBIC_INI))
    if database is not None:
        config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database}")
    return config


def _alembic_head() -> str:
    heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
    if len(heads) != 1:
        raise RuntimeError("closure evidence requires exactly one Alembic head")
    return str(heads[0])


def _openapi_evidence() -> dict[str, Any]:
    from mesa_memory.api.server import app

    schema = app.openapi()
    encoded = json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    operation_count = sum(
        1
        for methods in schema.get("paths", {}).values()
        for method in methods
        if method.lower() in {"get", "post", "put", "patch", "delete", "options"}
    )
    return {
        "sha256": _sha256(encoded),
        "path_count": len(schema.get("paths", {})),
        "operation_count": operation_count,
    }


def _suite_counts(element: ET.Element) -> dict[str, int | float]:
    suites = (
        [element] if element.tag == "testsuite" else list(element.findall("testsuite"))
    )
    if not suites:
        suites = list(element.findall(".//testsuite"))
    totals: dict[str, int | float] = {
        "tests": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "duration_seconds": 0.0,
    }
    for suite in suites:
        totals["tests"] += int(suite.attrib.get("tests", 0))
        totals["failures"] += int(suite.attrib.get("failures", 0))
        totals["errors"] += int(suite.attrib.get("errors", 0))
        totals["skipped"] += int(suite.attrib.get("skipped", 0))
        totals["duration_seconds"] += float(suite.attrib.get("time", 0.0))
    totals["duration_seconds"] = round(float(totals["duration_seconds"]), 3)
    return totals


def _junit_evidence(paths: list[Path]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    totals: dict[str, int | float] = {
        "tests": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "duration_seconds": 0.0,
    }
    for path in paths:
        if not path.is_file():
            records.append({"available": False})
            continue
        payload = path.read_bytes()
        if len(payload) > _MAX_JUNIT_BYTES:
            raise ValueError("JUnit evidence exceeds the bounded size limit")
        counts = _suite_counts(ET.fromstring(payload))
        records.append({"available": True, "sha256": _sha256(payload), **counts})
        for key in ("tests", "failures", "errors", "skipped"):
            totals[key] += int(counts[key])
        totals["duration_seconds"] += float(counts["duration_seconds"])
    totals["duration_seconds"] = round(float(totals["duration_seconds"]), 3)
    available = bool(records) and all(record["available"] for record in records)
    return {
        "available": available,
        "passed": available
        and int(totals["failures"]) == 0
        and int(totals["errors"]) == 0,
        "reports": records,
        "totals": totals,
    }


def _golden_ids() -> dict[str, str]:
    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=7,
        tenant_id="tenant-golden",
        workspace_id="workspace-golden",
        dataset_id="dataset-golden",
        document_id="document-golden",
        revision_id="revision-golden",
        chunk_id="chunk-golden",
        agent_id="agent-golden",
        session_id="session-golden",
        content_payload="fixture",
        source_ref="source-golden",
    )
    entity_id = MemoryDAO.v4_entity_id("tenant-golden", "  ALPHA  ")
    assertion_id = MemoryDAO.v4_assertion_id(
        tenant_id="tenant-golden",
        dataset_id="dataset-golden",
        revision_id="revision-golden",
        chunk_id="chunk-golden",
        subject_id=entity_id,
        predicate="RELATES_TO",
        literal_value="fixture",
        evidence_span="0:7",
    )
    return {
        "candidate_id": candidate.candidate_id,
        "mutation_id": candidate.mutation_id,
        "pipeline_run_id": str(candidate.pipeline_run_id),
        "entity_id": entity_id,
        "assertion_id": assertion_id,
    }


def _rrf_evidence() -> dict[str, Any]:
    corpus, qrels = fixed_legal_corpus()
    report = evaluate_lane_ablation(corpus, qrels)
    encoded = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "sha256": _sha256(encoded),
        "metric": str(report["metric"]),
        "lane_order": list(V4_RRF_LANE_ORDER),
        "query_count": len(qrels),
        "lane_set_count": len(report["scores"]),
        "scores": {
            key: round(float(value), 8)
            for key, value in sorted(report["scores"].items())
        },
        "delta_vs_vector": {
            key: round(float(value), 8)
            for key, value in sorted(report["delta_vs_vector"].items())
        },
    }


def _fixture_store_counts() -> dict[str, int]:
    with tempfile.TemporaryDirectory(prefix="mesa-v4-evidence-") as temp_root:
        database = Path(temp_root) / "fixture.sqlite"
        command.upgrade(_alembic_config(database), "head")
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "INSERT INTO v4_entities (entity_id, tenant_id, entity_type, "
                "canonical_name, normalized_name, identity_key) VALUES "
                "('entity-fixture', 'tenant-fixture', 'concept', 'Fixture', "
                "'fixture', 'entity-fixture')"
            )
            connection.execute(
                "INSERT INTO v4_assertions (assertion_id, tenant_id, dataset_id, "
                "subject_id, predicate, literal_value, source_ref, document_id, "
                "revision_id, chunk_id, evidence_span, confidence, status, "
                "mutation_id, pipeline_run_id) VALUES ('assertion-fixture', "
                "'tenant-fixture', 'dataset-fixture', 'entity-fixture', "
                "'RELATES_TO', 'fixture', 'source-fixture', 'document-fixture', "
                "'revision-fixture', 'chunk-fixture', '0:7', 1.0, 'ACTIVE', "
                "'mutation-fixture', 'pipeline-fixture')"
            )
            artifacts = (
                ("vector-fixture", "VECTOR", "ENTITY_VECTOR", "entity-fixture"),
                ("graph-entity-fixture", "GRAPH", "ENTITY", "entity-fixture"),
                (
                    "graph-assertion-fixture",
                    "GRAPH",
                    "ASSERTION",
                    "assertion-fixture",
                ),
            )
            for registry_id, store_name, artifact_kind, physical_id in artifacts:
                connection.execute(
                    "INSERT INTO artifact_registry (registry_id, tenant_id, "
                    "agent_id, dataset_id, store_name, artifact_kind, "
                    "physical_artifact_id, state) VALUES (?, 'tenant-fixture', "
                    "'agent-fixture', 'dataset-fixture', ?, ?, ?, 'ACTIVE')",
                    (registry_id, store_name, artifact_kind, physical_id),
                )
                connection.execute(
                    "INSERT INTO artifact_sources (source_ownership_id, "
                    "registry_id, mutation_id, pipeline_run_id, dataset_id, "
                    "source_ref, state) VALUES (?, ?, 'mutation-fixture', "
                    "'pipeline-fixture', 'dataset-fixture', 'source-fixture', "
                    "'ACTIVE')",
                    (f"source-{registry_id}", registry_id),
                )
            connection.commit()
            queries = {
                "canonical_entities": "SELECT COUNT(*) FROM v4_entities",
                "canonical_assertions": "SELECT COUNT(*) FROM v4_assertions",
                "fts_entities": "SELECT COUNT(*) FROM v4_entities_fts",
                "vector_registry_artifacts": (
                    "SELECT COUNT(*) FROM artifact_registry "
                    "WHERE store_name = 'VECTOR' AND state = 'ACTIVE'"
                ),
                "graph_registry_artifacts": (
                    "SELECT COUNT(*) FROM artifact_registry "
                    "WHERE store_name = 'GRAPH' AND state = 'ACTIVE'"
                ),
                "active_source_owners": (
                    "SELECT COUNT(*) FROM artifact_sources WHERE state = 'ACTIVE'"
                ),
                "projection_generations": (
                    "SELECT COUNT(*) FROM projection_generations"
                ),
                "active_runtime_pointers": ("SELECT COUNT(*) FROM projection_runtime"),
            }
            return {
                name: int(connection.execute(statement).fetchone()[0])
                for name, statement in queries.items()
            }
        finally:
            connection.close()


def build_manifest(junit_paths: list[Path]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "mesa-v4-closure-evidence",
        "alembic_head": _alembic_head(),
        "openapi": _openapi_evidence(),
        "tests": _junit_evidence(junit_paths),
        "golden_ids": _golden_ids(),
        "rrf_ablation": _rrf_evidence(),
        "fixture_store_counts": _fixture_store_counts(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write a content-free MESA V4 closure evidence manifest."
    )
    parser.add_argument("--junit", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = build_manifest(args.junit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
