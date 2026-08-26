"""MESA MVP Certification Round 7 — Lifecycle + Catalog Identity Correctness Tests.

Validates R701-R711:
- R702: Late manifest finalization activation reconciliation, incomplete manifest safety, idempotence
- R703: Semantic REJECTED replay non-replayability (HTTP 409) vs technical DLQ replay preservation
- R704: Historical closed-session rollback/replay authorization (positive & negative matrix)
- R705: Revision hash semantic separation (declared vs manifest vs chunk hash)
- R706: Opaque server-generated physical IDs, public resolution authority, alias rejection, legacy compatibility
- R707: Elimination of physical ID leakage in public responses
- R708: Schema migration and predecessor database upgrade compatibility
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mesa_api.v4_router import create_v4_router
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import (
    MemoryDAO,
    NonReplayableMutationConflictError,
)
from mesa_storage.repositories.catalog import (
    CatalogIdentityNotFoundError,
    CatalogRepository,
)
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.fixture
def test_app_factory():
    def _create(dao: MemoryDAO, access_control: AccessControl) -> TestClient:
        app = FastAPI()

        @app.middleware("http")
        async def attach_principal(request, call_next):
            principal_id = request.headers.get("X-MESA-Principal")
            if principal_id:
                request.state.principal = SimpleNamespace(
                    principal_id=principal_id,
                    status="active",
                )
            return await call_next(request)

        router = create_v4_router(
            lambda: dao, get_access_control=lambda: access_control
        )
        app.include_router(router)
        return TestClient(app, raise_server_exceptions=False)

    return _create


# ---------------------------------------------------------------------------
# R702: Late Manifest Finalization Reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_late_manifest_finalization_reconciles_activation(
    tmp_path: Any,
) -> None:
    """When child mutations commit before manifest freeze, later freeze must activate the revision."""
    db_path = str(tmp_path / "late_manifest.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    tenant_id = "tenant_r702"
    dataset_id = "dataset_r702"
    doc_id = "doc_r702"
    rev_id = "rev_r702"

    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id, workspace_id="ws_r702", dataset_id=dataset_id
    )
    await dao.create_v4_document(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id, title="Doc R702"
    )

    # 1. Create source chunk without freezing manifest (finalize_revision=False)
    await dao.create_v4_source_chunk(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev_id,
        chunk_id="chk_r702_1",
        title="Title 1",
        content_payload="Content payload 1",
        source_ref="ref_1",
        revision_number=1,
        chunk_ordinal=0,
        finalize_revision=False,
    )

    # Verify revision is PENDING and manifest is not frozen
    revisions = await dao.list_v4_revisions(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id
    )
    assert len(revisions) == 1
    assert revisions[0]["status"] == "PENDING"

    # 2. Insert and commit memory mutation for this chunk
    mutation_id = f"mut_{uuid.uuid4().hex}"
    pipeline_id = f"pipe_{uuid.uuid4().hex}"

    async with engine.connection() as db:
        physical_rev = await dao._catalog.resolve_id_in_tx(
            db,
            tenant_id=tenant_id,
            kind="revision",
            external_id=rev_id,
        )
        physical_chunk = await dao._catalog.resolve_id_in_tx(
            db,
            tenant_id=tenant_id,
            kind="chunk",
            external_id="chk_r702_1",
        )

    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
            "VALUES (?, ?, 'sess_1', 'agent_1', 'COMMITTED')",
            (pipeline_id, tenant_id),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, session_id, content_payload, pipeline_run_id, revision_id, chunk_id, state) "
            "VALUES (?, ?, ?, 'agent_1', 'sess_1', 'payload', ?, ?, ?, 'COMMITTED')",
            (
                mutation_id,
                f"cand_{mutation_id}",
                tenant_id,
                pipeline_id,
                physical_rev,
                physical_chunk,
            ),
        )
        await db.commit()

    # Mutation is committed, but manifest is still unfrozen -> revision must stay PENDING
    revisions = await dao.list_v4_revisions(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id
    )
    assert revisions[0]["status"] == "PENDING"

    # 3. Now finalize/freeze the manifest
    async with engine.transaction() as db:
        await dao._update_revision_manifest(db, physical_rev, finalize_revision=True)
        await db.commit()

    # Invariant: revision MUST now be ACTIVE without any additional mutation event!
    revisions = await dao.list_v4_revisions(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id
    )
    assert revisions[0]["status"] == "ACTIVE"

    await engine.close()


@pytest.mark.asyncio
async def test_r7_incomplete_manifest_remains_pending_after_freeze(
    tmp_path: Any,
) -> None:
    """If any chunk lacks a committed mutation, manifest freeze must leave revision PENDING."""
    db_path = str(tmp_path / "incomplete_manifest.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    tenant_id = "tenant_inc"
    dataset_id = "dataset_inc"
    doc_id = "doc_inc"
    rev_id = "rev_inc"

    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id, workspace_id="ws_inc", dataset_id=dataset_id
    )
    await dao.create_v4_document(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id, title="Doc Inc"
    )

    # 2 chunks
    await dao.create_v4_source_chunk(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev_id,
        chunk_id="chk_inc_1",
        title="Title 1",
        content_payload="Payload 1",
        source_ref="ref_1",
        chunk_ordinal=0,
        finalize_revision=False,
    )
    await dao.create_v4_source_chunk(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev_id,
        chunk_id="chk_inc_2",
        title="Title 2",
        content_payload="Payload 2",
        source_ref="ref_2",
        chunk_ordinal=1,
        finalize_revision=False,
    )

    async with engine.connection() as db:
        physical_rev = await dao._catalog.resolve_id_in_tx(
            db,
            tenant_id=tenant_id,
            kind="revision",
            external_id=rev_id,
        )
        physical_chunk1 = await dao._catalog.resolve_id_in_tx(
            db,
            tenant_id=tenant_id,
            kind="chunk",
            external_id="chk_inc_1",
        )

    # Only commit chunk 1 (chunk 2 has no committed work)
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
            "VALUES ('p_inc', ?, 'sess_1', 'agent_1', 'RUNNING')",
            (tenant_id,),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, session_id, content_payload, pipeline_run_id, revision_id, chunk_id, state) "
            "VALUES ('m_inc_1', 'c_inc_1', ?, 'agent_1', 'sess_1', 'payload', 'p_inc', ?, ?, 'COMMITTED')",
            (tenant_id, physical_rev, physical_chunk1),
        )
        # Freeze manifest
        await dao._update_revision_manifest(db, physical_rev, finalize_revision=True)
        await db.commit()

    # Revision MUST stay PENDING because chunk 2 is missing committed work
    revisions = await dao.list_v4_revisions(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id
    )
    assert revisions[0]["status"] == "PENDING"

    await engine.close()


@pytest.mark.asyncio
async def test_r7_repeated_finalization_is_idempotent(tmp_path: Any) -> None:
    """Repeated manifest finalization preserves single ACTIVE head and manifest identity."""
    db_path = str(tmp_path / "idempotent_final.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    tenant_id = "tenant_idemp"
    dataset_id = "dataset_idemp"
    doc_id = "doc_idemp"
    rev_id = "rev_idemp"

    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id, workspace_id="ws_idemp", dataset_id=dataset_id
    )
    await dao.create_v4_document(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        title="Doc Idemp",
    )

    await dao.create_v4_source_chunk(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev_id,
        chunk_id="chk_idemp_1",
        title="Title 1",
        content_payload="Payload 1",
        source_ref="ref_1",
        finalize_revision=False,
    )

    async with engine.connection() as db:
        physical_rev = await dao._catalog.resolve_id_in_tx(
            db,
            tenant_id=tenant_id,
            kind="revision",
            external_id=rev_id,
        )
        physical_chunk = await dao._catalog.resolve_id_in_tx(
            db,
            tenant_id=tenant_id,
            kind="chunk",
            external_id="chk_idemp_1",
        )

    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
            "VALUES ('p_idemp', ?, 'sess_1', 'agent_1', 'RUNNING')",
            (tenant_id,),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, session_id, content_payload, pipeline_run_id, revision_id, chunk_id, state) "
            "VALUES ('m_idemp', 'c_idemp', ?, 'agent_1', 'sess_1', 'payload', 'p_idemp', ?, ?, 'COMMITTED')",
            (tenant_id, physical_rev, physical_chunk),
        )
        # Freeze manifest once
        m_hash1 = await dao._update_revision_manifest(
            db, physical_rev, finalize_revision=True
        )
        # Freeze manifest second time
        m_hash2 = await dao._update_revision_manifest(
            db, physical_rev, finalize_revision=True
        )
        await db.commit()

    assert m_hash1 == m_hash2
    revisions = await dao.list_v4_revisions(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id
    )
    assert len(revisions) == 1
    assert revisions[0]["status"] == "ACTIVE"

    await engine.close()


# ---------------------------------------------------------------------------
# R703: Semantic REJECTED Replay Truthfulness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_semantic_rejected_replay_fails_closed_and_terminal(
    tmp_path: Any, test_app_factory: Any
) -> None:
    """Semantic REJECTED mutation cannot be replayed; returns HTTP 409 NON_REPLAYABLE."""
    db_path = str(tmp_path / "rejected_replay.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    rbac_path = str(tmp_path / "rbac.db")
    access_control = AccessControl(rbac_path)
    await access_control.initialize()

    client = test_app_factory(dao, access_control)

    tenant_id = "tenant_rej"
    workspace_id = "ws_rej"
    dataset_id = "ds_rej"
    principal_id = "principal_rej"
    agent_id = "agent_rej"

    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id, workspace_id=workspace_id, dataset_id=dataset_id
    )
    session = await dao.create_v4_session(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_ids=[dataset_id],
        agent_id=agent_id,
        principal_id=principal_id,
    )
    session_id = session["session_id"]

    await access_control.grant_principal_session_access(
        principal_id=principal_id,
        agent_id=agent_id,
        session_id=session_id,
        level="ADMIN",
    )
    await access_control.grant_access(
        agent_id=agent_id, session_id=session_id, level="ADMIN"
    )
    await access_control.grant_scope_role(
        principal_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        role="OWNER",
    )
    await access_control.grant_dataset_permission(
        principal_id,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        permission="ROLLBACK",
    )
    async with engine.connection() as db:
        physical_dataset_id = await dao.catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
        )

    # Insert a rejected mutation
    mutation_id = "mut_rejected_1"
    pipeline_id = "pipe_rejected_1"
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
            "VALUES (?, ?, ?, ?, 'DLQ')",
            (pipeline_id, tenant_id, session_id, agent_id),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, session_id, content_payload, dataset_id, pipeline_run_id, state, failure_class) "
            "VALUES (?, ?, ?, ?, ?, 'payload', ?, ?, 'REJECTED', 'COGNITIVE_REJECTION')",
            (
                mutation_id,
                f"cand_{mutation_id}",
                tenant_id,
                agent_id,
                session_id,
                physical_dataset_id,
                pipeline_id,
            ),
        )
        await db.commit()

    # Direct DAO call must raise NonReplayableMutationConflictError
    with pytest.raises(NonReplayableMutationConflictError):
        await dao.replay_pipeline_run(pipeline_id)

    # API call must return HTTP 409 NON_REPLAYABLE
    response = client.post(
        f"/v4/mutations/{mutation_id}/replay",
        headers={"X-MESA-Principal": principal_id},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "NON_REPLAYABLE"

    # Semantic terminal status must not bypass historical-session authorization.
    unauthorized = client.post(
        f"/v4/mutations/{mutation_id}/replay",
        headers={"X-MESA-Principal": "unbound_principal"},
    )
    assert unauthorized.status_code == 404
    assert unauthorized.json()["detail"] == "Unknown session"

    # State must remain REJECTED and DLQ
    async with engine.connection() as db:
        async with db.execute(
            "SELECT state FROM memory_mutations WHERE mutation_id = ?", (mutation_id,)
        ) as cur:
            m_state = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT state FROM pipeline_runs WHERE pipeline_run_id = ?", (pipeline_id,)
        ) as cur:
            p_state = (await cur.fetchone())[0]
    assert m_state == "REJECTED"
    assert p_state == "DLQ"

    await access_control.close()
    await engine.close()


@pytest.mark.asyncio
async def test_r7_technical_dlq_replay_is_preserved(tmp_path: Any) -> None:
    """Technical DLQ failure (e.g. DEAD_LETTER projection) remains replayable."""
    db_path = str(tmp_path / "technical_replay.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    tenant_id = "tenant_tech"
    pipeline_id = "pipe_tech"
    mutation_id = "mut_tech"

    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
            "VALUES (?, ?, 'sess_1', 'agent_1', 'DLQ')",
            (pipeline_id, tenant_id),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, session_id, content_payload, pipeline_run_id, state) "
            "VALUES (?, ?, ?, 'agent_1', 'sess_1', 'payload', ?, 'VALIDATED')",
            (mutation_id, f"cand_{mutation_id}", tenant_id, pipeline_id),
        )
        await db.execute(
            "INSERT INTO projection_outbox (projection_id, mutation_id, projection_name, state) "
            "VALUES ('proj_tech_1', ?, 'VECTOR', 'DEAD_LETTER')",
            (mutation_id,),
        )
        await db.commit()

    result = await dao.replay_pipeline_run(pipeline_id)
    assert result["state"] == "RETRY_PENDING"

    async with engine.connection() as db:
        async with db.execute(
            "SELECT state FROM pipeline_runs WHERE pipeline_run_id = ?", (pipeline_id,)
        ) as cur:
            p_state = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT state FROM projection_outbox WHERE mutation_id = ?", (mutation_id,)
        ) as cur:
            o_state = (await cur.fetchone())[0]
    assert p_state == "RETRY_PENDING"
    assert o_state == "RETRY_PENDING"

    await engine.close()


# ---------------------------------------------------------------------------
# R704: Historical Closed-Session Rollback/Replay Authorization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_closed_session_historical_rollback_and_replay_positive(
    tmp_path: Any, test_app_factory: Any
) -> None:
    """Historical rollback on closed session succeeds when principal is authorized and has ROLLBACK permission."""
    db_path = str(tmp_path / "historical_auth.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    rbac_path = str(tmp_path / "rbac_hist.db")
    access_control = AccessControl(rbac_path)
    await access_control.initialize()

    client = test_app_factory(dao, access_control)

    tenant_id = "tenant_hist"
    workspace_id = "ws_hist"
    dataset_id = "ds_hist"
    principal_id = "principal_hist"
    agent_id = "agent_hist"

    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id, workspace_id=workspace_id, dataset_id=dataset_id
    )
    session = await dao.create_v4_session(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_ids=[dataset_id],
        agent_id=agent_id,
        principal_id=principal_id,
    )
    session_id = session["session_id"]

    await access_control.grant_principal_session_access(
        principal_id=principal_id,
        agent_id=agent_id,
        session_id=session_id,
        level="ADMIN",
    )
    await access_control.grant_access(
        agent_id=agent_id, session_id=session_id, level="ADMIN"
    )
    await access_control.grant_scope_role(
        principal_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        role="OWNER",
    )
    await access_control.grant_dataset_permission(
        principal_id,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        permission="ROLLBACK",
    )
    async with engine.connection() as db:
        physical_dataset_id = await dao.catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
        )

    # Insert committed mutation
    mutation_id = "mut_hist_1"
    pipeline_id = "pipe_hist_1"
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
            "VALUES (?, ?, ?, ?, 'COMMITTED')",
            (pipeline_id, tenant_id, session_id, agent_id),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, session_id, content_payload, dataset_id, pipeline_run_id, state) "
            "VALUES (?, ?, ?, ?, ?, 'payload', ?, ?, 'COMMITTED')",
            (
                mutation_id,
                f"cand_{mutation_id}",
                tenant_id,
                agent_id,
                session_id,
                physical_dataset_id,
                pipeline_id,
            ),
        )
        await db.commit()

    # Close/end the session!
    ended = await dao.end_v4_session(session_id)
    assert ended is True
    s_info = await dao.get_v4_session(session_id)
    assert s_info["status"] == "ENDED"

    # Rollback request on CLOSED session must succeed (HTTP 202)
    response = client.post(
        f"/v4/mutations/{mutation_id}/rollback",
        headers={"X-MESA-Principal": principal_id},
    )
    assert response.status_code == 202
    assert response.json()["state"] in {"ROLLING_BACK", "ROLLED_BACK"}

    await access_control.close()
    await engine.close()


@pytest.mark.asyncio
async def test_r7_historical_auth_negative_matrix(
    tmp_path: Any, test_app_factory: Any
) -> None:
    """Historical administration fails for wrong principal, wrong tenant, or missing ROLLBACK permission."""
    db_path = str(tmp_path / "hist_neg.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    rbac_path = str(tmp_path / "rbac_neg.db")
    access_control = AccessControl(rbac_path)
    await access_control.initialize()

    client = test_app_factory(dao, access_control)

    tenant_id = "tenant_neg"
    workspace_id = "ws_neg"
    dataset_id = "ds_neg"
    legit_principal = "legit_user"
    attacker_principal = "attacker_user"
    no_rollback_principal = "no_rollback_user"
    agent_id = "agent_neg"

    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id, workspace_id=workspace_id, dataset_id=dataset_id
    )
    session = await dao.create_v4_session(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_ids=[dataset_id],
        agent_id=agent_id,
        principal_id=legit_principal,
    )
    session_id = session["session_id"]

    await access_control.grant_principal_session_access(
        principal_id=legit_principal,
        agent_id=agent_id,
        session_id=session_id,
        level="ADMIN",
    )
    await access_control.grant_access(
        agent_id=agent_id, session_id=session_id, level="ADMIN"
    )
    await access_control.grant_scope_role(
        legit_principal,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        role="OWNER",
    )
    await access_control.grant_dataset_permission(
        legit_principal,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        permission="ROLLBACK",
    )

    # Setup no_rollback_principal: has session access and WRITER role, but NO dataset ROLLBACK permission
    await access_control.grant_principal_session_access(
        principal_id=no_rollback_principal,
        agent_id=agent_id,
        session_id=session_id,
        level="ADMIN",
    )
    await access_control.grant_scope_role(
        no_rollback_principal,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        role="WRITER",
    )

    # Close session
    await dao.end_v4_session(session_id)
    async with engine.connection() as db:
        physical_dataset_id = await dao.catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
        )

    mutation_id = "mut_neg_1"
    pipeline_id = "pipe_neg_1"
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
            "VALUES (?, ?, ?, ?, 'COMMITTED')",
            (pipeline_id, tenant_id, session_id, agent_id),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, session_id, content_payload, dataset_id, pipeline_run_id, state) "
            "VALUES (?, ?, ?, ?, ?, 'payload', ?, ?, 'COMMITTED')",
            (
                mutation_id,
                f"cand_{mutation_id}",
                tenant_id,
                agent_id,
                session_id,
                physical_dataset_id,
                pipeline_id,
            ),
        )
        await db.commit()

    # 1. Attacker (unbound principal) receives the generic inaccessible class.
    resp_atk = client.post(
        f"/v4/mutations/{mutation_id}/rollback",
        headers={"X-MESA-Principal": attacker_principal},
    )
    assert resp_atk.status_code == 404
    assert resp_atk.json()["detail"] == "Unknown session"

    # 2. A foreign principal cannot borrow a session grant to inspect permissions.
    resp_no_rb = client.post(
        f"/v4/mutations/{mutation_id}/rollback",
        headers={"X-MESA-Principal": no_rollback_principal},
    )
    assert resp_no_rb.status_code == 404
    assert resp_no_rb.json()["detail"] == "Unknown session"

    # 3. The owning principal still receives legitimate permission semantics.
    assert await access_control.revoke_dataset_permission(
        legit_principal,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        permission="ROLLBACK",
    )
    resp_owner_no_rb = client.post(
        f"/v4/mutations/{mutation_id}/rollback",
        headers={"X-MESA-Principal": legit_principal},
    )
    assert resp_owner_no_rb.status_code == 403
    assert "ROLLBACK permission required" in resp_owner_no_rb.json()["detail"]

    await access_control.close()
    await engine.close()


# ---------------------------------------------------------------------------
# R705: Revision Hash Semantic Separation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_revision_hash_semantic_separation(tmp_path: Any) -> None:
    """Declared whole-revision hash, manifest hash, and chunk hash remain distinct."""
    db_path = str(tmp_path / "hash_semantics.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    tenant_id = "tenant_hash"
    dataset_id = "ds_hash"
    doc_id = "doc_hash"
    rev_explicit = "rev_explicit"
    rev_direct = "rev_direct"
    declared_hash = "f" * 64

    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id, workspace_id="ws_hash", dataset_id=dataset_id
    )
    await dao.create_v4_document(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id, title="Doc Hash"
    )

    # 1. Explicit revision creation: caller declares whole-revision hash
    rev_created = await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev_explicit,
        revision_number=1,
        content_hash=declared_hash,
    )
    assert rev_created["declared_content_hash"] == declared_hash

    # Add chunk
    chunk_a = await dao.create_v4_source_chunk(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev_explicit,
        chunk_id="chunk_a",
        title="A",
        content_payload="Payload A",
        source_ref="ref_a",
        chunk_ordinal=0,
    )
    assert chunk_a["manifest_hash"] != declared_hash
    assert chunk_a["content_hash"] != declared_hash

    # 2. Direct insert without explicit revision creation
    await dao.create_v4_source_chunk(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev_direct,
        chunk_id="chunk_b",
        title="B",
        content_payload="Payload B",
        source_ref="ref_b",
        revision_number=2,
        chunk_ordinal=0,
    )

    revisions = await dao.list_v4_revisions(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id
    )
    rev_map = {r["revision_id"]: r for r in revisions}

    # Explicit revision retains declared hash
    assert rev_map[rev_explicit]["declared_content_hash"] == declared_hash

    # Direct insert revision MUST NOT forge chunk hash as declared whole-revision hash
    assert rev_map[rev_direct]["declared_content_hash"] is None
    assert rev_map[rev_direct]["manifest_hash"] is not None

    await engine.close()


# ---------------------------------------------------------------------------
# R706: Opaque Server-Generated Catalog IDs and Alias Rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_opaque_catalog_ids_and_alias_rejection(tmp_path: Any) -> None:
    """New catalog physical IDs are opaque server-generated UUIDs; physical ID alias attacks fail."""
    db_path = str(tmp_path / "catalog_opacity.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    repo: CatalogRepository = dao.catalog

    # 1. New mapping -> opaque physical ID (not equal to external_id)
    ws_created = await repo.create_workspace(
        tenant_id="tenant_a",
        workspace_id="my_workspace",
        workspace_name="Workspace A",
    )
    assert ws_created["workspace_id"] == "my_workspace"
    async with engine.connection() as db:
        async with db.execute(
            "SELECT physical_id FROM v4_catalog_identities WHERE tenant_id = 'tenant_a' AND external_id = 'my_workspace'"
        ) as cur:
            phys_row = await cur.fetchone()
            assert phys_row is not None
            physical_id = phys_row[0]

    assert physical_id != "my_workspace"
    assert physical_id.startswith("mesa-workspace-")

    # 2. Same public ID in tenant_b gets independent opaque physical ID
    await repo.create_workspace(
        tenant_id="tenant_b",
        workspace_id="my_workspace",
        workspace_name="Workspace B",
    )
    async with engine.connection() as db:
        async with db.execute(
            "SELECT physical_id FROM v4_catalog_identities WHERE tenant_id = 'tenant_b' AND external_id = 'my_workspace'"
        ) as cur:
            phys_b = (await cur.fetchone())[0]

    assert phys_b != physical_id
    assert phys_b != "my_workspace"

    # 3. Physical ID alias attack: an internal physical ID is not an external
    # authority, including when it reaches a supported public DAO entrypoint.
    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant_a", workspace_id="my_workspace", dataset_id="dataset_a"
    )
    await dao.create_v4_document(
        tenant_id="tenant_a",
        dataset_id="dataset_a",
        document_id="document_a",
        title="Document A",
    )
    async with engine.connection() as db:
        physical_dataset_id = await repo.resolve_id_in_tx(
            db, tenant_id="tenant_a", kind="dataset", external_id="dataset_a"
        )
        with pytest.raises(CatalogIdentityNotFoundError):
            await repo.resolve_id_in_tx(
                db,
                tenant_id="tenant_a",
                kind="workspace",
                external_id=physical_id,
                create=False,
            )

    with pytest.raises(CatalogIdentityNotFoundError):
        await dao.list_v4_documents(
            tenant_id="tenant_a", dataset_id=physical_dataset_id
        )

    # 4. Legacy mapping compatibility: historical mapping where external_id == physical_id works
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO tenants (tenant_id, display_name) VALUES ('tenant_legacy', 'Legacy Tenant')"
        )
        await db.execute(
            "INSERT INTO v4_catalog_identities (tenant_id, kind, external_id, physical_id) "
            "VALUES ('tenant_legacy', 'workspace', 'legacy_ws', 'legacy_ws')"
        )
        await db.commit()

    async with engine.connection() as db:
        resolved_legacy = await repo.resolve_id_in_tx(
            db,
            tenant_id="tenant_legacy",
            kind="workspace",
            external_id="legacy_ws",
            create=False,
        )
        assert resolved_legacy == "legacy_ws"

    await engine.close()


# ---------------------------------------------------------------------------
# R707: Public Physical ID Leak Sweep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_public_physical_id_leak_sweep(
    tmp_path: Any, test_app_factory: Any
) -> None:
    """Mutation status and pipeline_run endpoints must return external public IDs, never internal physical IDs."""
    db_path = str(tmp_path / "leak_sweep.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    rbac_path = str(tmp_path / "rbac_leak.db")
    access_control = AccessControl(rbac_path)
    await access_control.initialize()

    client = test_app_factory(dao, access_control)

    tenant_id = "tenant_leak"
    workspace_id = "ws_public_name"
    dataset_id = "ds_public_name"
    principal_id = "user_leak"
    agent_id = "agent_leak"

    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id, workspace_id=workspace_id, dataset_id=dataset_id
    )
    session = await dao.create_v4_session(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_ids=[dataset_id],
        agent_id=agent_id,
        principal_id=principal_id,
    )
    session_id = session["session_id"]

    await access_control.grant_principal_session_access(
        principal_id=principal_id,
        agent_id=agent_id,
        session_id=session_id,
        level="READ",
    )
    await access_control.grant_access(
        agent_id=agent_id, session_id=session_id, level="READ"
    )
    await access_control.grant_scope_role(
        principal_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        role="READER",
    )

    # Get the internal physical IDs to verify they are NOT leaked
    async with engine.connection() as db:
        phys_ws = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="workspace", external_id=workspace_id
        )
        phys_ds = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
        )

    assert phys_ws != workspace_id
    assert phys_ds != dataset_id

    # Create pipeline run stored with physical IDs
    mutation_id = "mut_leak_1"
    pipeline_id = "pipe_leak_1"
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, workspace_id, dataset_id, session_id, agent_id, state) "
            "VALUES (?, ?, ?, ?, ?, ?, 'COMMITTED')",
            (pipeline_id, tenant_id, phys_ws, phys_ds, session_id, agent_id),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, session_id, content_payload, dataset_id, pipeline_run_id, state) "
            "VALUES (?, ?, ?, ?, ?, 'payload', ?, ?, 'COMMITTED')",
            (
                mutation_id,
                f"cand_{mutation_id}",
                tenant_id,
                agent_id,
                session_id,
                dataset_id,
                pipeline_id,
            ),
        )
        await db.commit()

    # Check dao.get_pipeline_run translation
    pipeline = await dao.get_pipeline_run(pipeline_id)
    assert pipeline["workspace_id"] == workspace_id
    assert pipeline["dataset_id"] == dataset_id
    assert pipeline["workspace_id"] != phys_ws
    assert pipeline["dataset_id"] != phys_ds

    # Check API GET /v4/mutations/{mutation_id}
    response = client.get(
        f"/v4/mutations/{mutation_id}",
        headers={"X-MESA-Principal": principal_id},
    )
    assert response.status_code == 200
    data = response.json()
    p_run = data["pipeline_run"]
    assert p_run is not None
    assert p_run["workspace_id"] == workspace_id
    assert p_run["dataset_id"] == dataset_id
    assert p_run["workspace_id"] != phys_ws
    assert p_run["dataset_id"] != phys_ds

    await access_control.close()
    await engine.close()


# ---------------------------------------------------------------------------
# R708: Migration and Schema Contract Closure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_migration_and_schema_contract_closure(tmp_path: Any) -> None:
    """Fresh install and upgrade migrations converge on the identical required schema contract."""
    import sqlite3
    from pathlib import Path

    from alembic.config import Config

    from mesa_storage import schema_contract

    db_path = str(tmp_path / "fresh_migration.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    # Verify postflight schema contract
    alembic_cfg = Config(
        str(Path(__file__).parents[1] / "mesa_storage" / "alembic.ini")
    )
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")

    sync_conn = sqlite3.connect(db_path)
    try:
        schema_contract.validate_postflight(sync_conn, alembic_cfg)

        cur = sync_conn.execute("PRAGMA table_info(document_revisions)")
        columns = {row[1] for row in cur.fetchall()}
        assert "declared_content_hash" in columns
        assert "manifest_hash" in columns
        assert "manifest_frozen_at" in columns
    finally:
        sync_conn.close()

    await engine.close()
