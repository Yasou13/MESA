"""Deterministic assertion-poisoning guardrail tests against canonical SQLite."""

from __future__ import annotations

import pytest

from mesa_evals import legal_audit
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


async def _seed_assertions(
    db_path: str, rows: list[tuple[str, str, str, str]]
) -> None:
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(sqlite_engine=engine, vector_engine=None)
    await dao.create_v4_workspace(
        tenant_id="tenant-a", workspace_id="ws-1", workspace_name="WS 1"
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant-a", workspace_id="ws-1", dataset_id="ds-1"
    )
    try:
        async with engine.transaction() as db:
            ds_id = await dao._catalog.resolve_id_in_tx(
                db, tenant_id="tenant-a", kind="dataset", external_id="ds-1"
            )
            for idx, (agent_id, source_name, target_name, predicate) in enumerate(rows):
                mut_id = f"mut-{idx}"
                pipe_id = f"pipe-{idx}"
                subj_id = f"subj-{idx}"
                obj_id = f"obj-{idx}"
                ast_id = f"ast-{idx}"
                await db.execute(
                    "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
                    "VALUES (?, 'tenant-a', 'sess-1', ?, 'COMPLETED')",
                    (pipe_id, agent_id),
                )
                await db.execute(
                    "INSERT INTO memory_mutations (mutation_id, candidate_id, session_id, agent_id, tenant_id, content_payload, state) "
                    "VALUES (?, ?, 'sess-1', ?, 'tenant-a', 'payload', 'COMMITTED')",
                    (mut_id, f"cand-{idx}", agent_id),
                )
                await db.execute(
                    "INSERT INTO v4_entities (entity_id, tenant_id, entity_type, canonical_name, normalized_name, identity_key) "
                    "VALUES (?, 'tenant-a', 'CONCEPT', ?, ?, ?)",
                    (subj_id, source_name, source_name.lower(), f"key-s-{idx}"),
                )
                await db.execute(
                    "INSERT INTO v4_entities (entity_id, tenant_id, entity_type, canonical_name, normalized_name, identity_key) "
                    "VALUES (?, 'tenant-a', 'LAW', ?, ?, ?)",
                    (obj_id, target_name, target_name.lower(), f"key-o-{idx}"),
                )
                await db.execute(
                    "INSERT INTO v4_assertions ("
                    "assertion_id, tenant_id, dataset_id, subject_id, predicate, object_entity_id, "
                    "source_ref, document_id, revision_id, chunk_id, confidence, status, mutation_id, pipeline_run_id"
                    ") VALUES (?, 'tenant-a', ?, ?, ?, ?, '', '', '', '', 1.0, 'ACTIVE', ?, ?)",
                    (ast_id, ds_id, subj_id, predicate, obj_id, mut_id, pipe_id),
                )
            await db.commit()
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_graph_poisoning_audit_scopes_tenant_and_blocks_invalid_law(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "mesa-audit.db")
    await _seed_assertions(
        db_path,
        [
            ("tenant-a", "Dava Konusu", "TBK Madde 49", "ATIF_YAPAR"),
            ("tenant-a", "Dava Konusu", "Uydurma Kanun Madde 999", "ATIF_YAPAR"),
            ("tenant-b", "Dava Konusu", "Foreign Fake Law 777", "ATIF_YAPAR"),
        ],
    )

    result = await legal_audit.audit_graph(db_path, "tenant-a", threshold=0.05)

    assert result.total_legal_edges == 2
    assert result.valid_edges == 1
    assert result.invalid_count == 1
    with pytest.raises(legal_audit.GraphPoisoningError):
        legal_audit.enforce_guardrail(result, threshold=0.05)


@pytest.mark.asyncio
async def test_graph_poisoning_audit_accepts_only_known_law_references(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "mesa-audit-valid.db")
    await _seed_assertions(
        db_path,
        [
            ("tenant-a", "Dava Konusu", "TBK m.49", "DAYANIR"),
            ("tenant-b", "Dava Konusu", "Uydurma Kanun Madde 999", "DAYANIR"),
        ],
    )

    result = await legal_audit.audit_graph(db_path, "tenant-a")

    assert result.total_legal_edges == 1
    assert result.valid_edges == 1
    assert result.invalid_count == 0
    legal_audit.enforce_guardrail(result)
