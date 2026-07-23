"""V4 canonical catalog, shared ownership and dataset ACL contracts."""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_workers.projection_worker import process_artifact_cleanup_once


@pytest.mark.asyncio
async def test_catalog_hierarchy_is_listable_and_revisions_are_immutable(
    tmp_path,
) -> None:
    engine = AsyncEngine(str(tmp_path / "hierarchy.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    first_hash = hashlib.sha256(b"revision one").hexdigest()
    second_hash = hashlib.sha256(b"revision two").hexdigest()
    try:
        workspace = await dao.create_v4_workspace(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            workspace_name="Legal",
        )
        assert workspace["tenant_id"] == "tenant-a"
        assert [item["workspace_id"] for item in await dao.list_v4_workspaces(
            tenant_id="tenant-a"
        )] == ["workspace-a"]
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            dataset_id="dataset-a",
        )
        await dao.create_v4_document(
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            document_id="document-a",
            title="Kanun",
        )
        first = await dao.create_v4_revision(
            tenant_id="tenant-a",
            document_id="document-a",
            revision_id="revision-1",
            revision_number=1,
            content_hash=first_hash,
        )
        assert first["status"] == "ACTIVE"
        await dao.create_v4_revision(
            tenant_id="tenant-a",
            document_id="document-a",
            revision_id="revision-2",
            revision_number=2,
            content_hash=second_hash,
            supersedes_revision_id="revision-1",
        )
        revisions = await dao.list_v4_revisions(
            tenant_id="tenant-a", document_id="document-a"
        )
        assert [item["status"] for item in revisions] == ["SUPERSEDED", "ACTIVE"]
        with pytest.raises(ValueError, match="immutable"):
            await dao.create_v4_revision(
                tenant_id="tenant-a",
                document_id="document-a",
                revision_id="revision-2",
                revision_number=2,
                content_hash=first_hash,
                supersedes_revision_id="revision-1",
            )
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_source_chunk_id_cannot_be_reused_for_different_content(tmp_path) -> None:
    engine = AsyncEngine(str(tmp_path / "chunk-immutability.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    try:
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            dataset_id="dataset-a",
        )
        common = {
            "tenant_id": "tenant-a",
            "dataset_id": "dataset-a",
            "document_id": "document-a",
            "revision_id": "revision-a",
            "chunk_id": "chunk-a",
            "title": "Belge",
            "source_ref": "source-a",
        }
        await dao.create_v4_source_chunk(content_payload="ilk içerik", **common)
        with pytest.raises(ValueError, match="immutable"):
            await dao.create_v4_source_chunk(
                content_payload="değiştirilmiş içerik", **common
            )
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_canonical_entity_identity_is_tenant_type_and_ontology_scoped(
    tmp_path,
) -> None:
    engine = AsyncEngine(str(tmp_path / "catalog.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    try:
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            dataset_id="dataset-a",
        )
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-b",
            workspace_id="workspace-b",
            dataset_id="dataset-b",
        )
        first = await dao.resolve_v4_entity(
            tenant_id="tenant-a",
            canonical_name="  Anayasa   Mahkemesi ",
            aliases=["AYM"],
        )
        alias = await dao.resolve_v4_entity(
            tenant_id="tenant-a",
            canonical_name="aym",
        )
        other_tenant = await dao.resolve_v4_entity(
            tenant_id="tenant-b",
            canonical_name="Anayasa Mahkemesi",
        )
        ontology = await dao.resolve_v4_entity(
            tenant_id="tenant-a",
            canonical_name="Kişisel Verilerin Korunması Kanunu",
            ontology_uri="MEVZUAT:6698",
            aliases=["KVKK"],
        )
        ontology_again = await dao.resolve_v4_entity(
            tenant_id="tenant-a",
            canonical_name="KVKK",
            ontology_uri="mevzuat:6698",
        )
        name_only = await dao.resolve_v4_entity(
            tenant_id="tenant-a",
            canonical_name="Tüketicinin Korunması Kanunu",
        )
        ontology_upgrade = await dao.resolve_v4_entity(
            tenant_id="tenant-a",
            canonical_name="Tüketicinin Korunması Kanunu",
            ontology_uri="mevzuat:6502",
        )
        upgraded_alias = await dao.resolve_v4_entity(
            tenant_id="tenant-a",
            canonical_name="Tüketicinin Korunması Kanunu",
        )

        assert first["entity_id"] == alias["entity_id"]
        assert first["entity_id"] != other_tenant["entity_id"]
        assert ontology["entity_id"] == ontology_again["entity_id"]
        assert ontology_upgrade["entity_id"] == dao.v4_entity_id(
            "tenant-a",
            "Tüketicinin Korunması Kanunu",
            ontology_uri="mevzuat:6502",
        )
        assert ontology_upgrade["entity_id"] != name_only["entity_id"]
        assert upgraded_alias["entity_id"] == ontology_upgrade["entity_id"]
        async with engine.connection() as db:
            async with db.execute(
                "SELECT status, redirect_entity_id FROM v4_entities "
                "WHERE entity_id = ?",
                (name_only["entity_id"],),
            ) as cursor:
                redirect = await cursor.fetchone()
        assert tuple(redirect) == ("REDIRECTED", ontology_upgrade["entity_id"])
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_rollback_preserves_shared_entity_until_last_owner_is_released(
    tmp_path,
) -> None:
    engine = AsyncEngine(str(tmp_path / "ownership.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    first = MemoryCandidate.from_raw_log(
        raw_log_id=1,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        dataset_id="dataset-a",
        document_id="document-a",
        revision_id="revision-a",
        chunk_id="chunk-a",
        source_ref="source-a",
        agent_id="agent-a",
        session_id="session-a",
        content_payload="Anayasa Mahkemesi",
    ).as_consolidation_record()
    second = MemoryCandidate.from_raw_log(
        raw_log_id=2,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        dataset_id="dataset-a",
        document_id="document-b",
        revision_id="revision-b",
        chunk_id="chunk-b",
        source_ref="source-b",
        agent_id="agent-a",
        session_id="session-a",
        content_payload="Anayasa Mahkemesi",
    ).as_consolidation_record()
    try:
        await dao.record_mutation(first, raw_log_id=1)
        await dao.record_mutation(second, raw_log_id=2)
        first_entity = await dao.project_v4_sql_entity(
            mutation=first, entity_name="Anayasa Mahkemesi"
        )
        second_entity = await dao.project_v4_sql_entity(
            mutation=second, entity_name=" anayasa  mahkemesi "
        )
        assert first_entity == second_entity

        first_rollback = await dao.request_pipeline_rollback(
            str(first["pipeline_run_id"])
        )
        assert first_rollback["cleanup_count"] == 0
        async with engine.connection() as db:
            async with db.execute(
                "SELECT status FROM v4_entities WHERE entity_id = ?",
                (first_entity,),
            ) as cursor:
                row = await cursor.fetchone()
        assert row is not None and row[0] == "ACTIVE"

        second_rollback = await dao.request_pipeline_rollback(
            str(second["pipeline_run_id"])
        )
        assert second_rollback["cleanup_count"] == 1
        cleanup = await process_artifact_cleanup_once(dao, worker_id="cleanup-a")
        assert cleanup["completed"] == 1
        assert (await dao.get_pipeline_run(str(second["pipeline_run_id"])))["state"] == (
            "ROLLED_BACK"
        )
        async with engine.connection() as db:
            async with db.execute(
                "SELECT 1 FROM v4_entities WHERE entity_id = ?",
                (first_entity,),
            ) as cursor:
                assert await cursor.fetchone() is None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_dataset_roles_do_not_cross_tenant_or_dataset(tmp_path) -> None:
    access = AccessControl(str(tmp_path / "rbac.sqlite"))
    await access.initialize()
    try:
        await access.grant_scope_role(
            "principal-a",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            dataset_id="dataset-a",
            role="WRITER",
        )
        assert await access.check_scope_role(
            "principal-a",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            dataset_id="dataset-a",
            required_role="READER",
        )
        assert not await access.check_scope_role(
            "principal-a",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            dataset_id="dataset-b",
            required_role="READER",
        )
        assert not await access.check_scope_role(
            "principal-a",
            tenant_id="tenant-b",
            workspace_id="workspace-a",
            dataset_id="dataset-a",
            required_role="READER",
        )
    finally:
        await access.close()


@pytest.mark.asyncio
async def test_bidirectional_reconciliation_quarantines_physical_sql_orphan(
    tmp_path,
) -> None:
    engine = AsyncEngine(str(tmp_path / "reconcile.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    vector = SimpleNamespace(
        get_active_node_ids=AsyncMock(return_value=[]),
    )
    dao = MemoryDAO(engine, vector)
    try:
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            dataset_id="dataset-a",
        )
        entity = await dao.resolve_v4_entity(
            tenant_id="tenant-a", canonical_name="Orphan Entity"
        )
        observed = await dao.reconcile_v4_bidirectional(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
        )
        assert observed["physical_orphans"] == 1

        repaired = await dao.reconcile_v4_bidirectional(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            repair=True,
        )
        assert repaired["cleanup_enqueued"] == 1
        cleanup = await process_artifact_cleanup_once(dao, worker_id="cleanup-a")
        assert cleanup["completed"] == 1
        async with engine.connection() as db:
            async with db.execute(
                "SELECT 1 FROM v4_entities WHERE entity_id = ?",
                (entity["entity_id"],),
            ) as cursor:
                assert await cursor.fetchone() is None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_v4_search_filters_vector_and_lexical_lanes_before_rrf(tmp_path) -> None:
    engine = AsyncEngine(str(tmp_path / "search.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    vector = SimpleNamespace(
        compute_embedding=AsyncMock(return_value=[1.0, 0.0]),
        upsert=AsyncMock(),
        search=AsyncMock(),
    )
    dao = MemoryDAO(engine, vector)
    allowed = MemoryCandidate.from_raw_log(
        raw_log_id=11,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        dataset_id="dataset-a",
        document_id="document-a",
        revision_id="revision-a",
        chunk_id="chunk-a",
        source_ref="source-a",
        agent_id="agent-a",
        session_id="session-a",
        content_payload="Allowed Court",
    ).as_consolidation_record()
    denied = MemoryCandidate.from_raw_log(
        raw_log_id=12,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        dataset_id="dataset-b",
        document_id="document-b",
        revision_id="revision-b",
        chunk_id="chunk-b",
        source_ref="source-b",
        agent_id="agent-a",
        session_id="session-a",
        content_payload="Denied Court",
    ).as_consolidation_record()
    try:
        await dao.record_mutation(allowed, raw_log_id=11)
        await dao.record_mutation(denied, raw_log_id=12)
        allowed_id = await dao.project_v4_sql_entity(
            mutation=allowed, entity_name="Allowed Court"
        )
        denied_id = await dao.project_v4_sql_entity(
            mutation=denied, entity_name="Denied Court"
        )
        await dao.project_v4_vector_entity(
            mutation=allowed, entity_name="Allowed Court"
        )
        await dao.project_v4_vector_entity(
            mutation=denied, entity_name="Denied Court"
        )
        vector.search.return_value = [
            {"node_id": denied_id, "_distance": 0.01},
            {"node_id": allowed_id, "_distance": 0.02},
        ]

        results = await dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="Court",
        )
        assert [item["entity"]["entity_id"] for item in results] == [allowed_id]
    finally:
        await engine.close()
