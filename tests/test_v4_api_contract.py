"""V4 API admission and principal/session authorization contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mesa_api.v4_router import create_v4_router
from mesa_storage.dao import (
    QueueOverCapacityError,
    QueueRecordTooLargeError,
    QueueUnavailableError,
)


def _app(dao, access_control, *, principal_id: str = "principal-a") -> TestClient:  # type: ignore[no-untyped-def]
    app = FastAPI()

    @app.middleware("http")
    async def attach_principal(request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = SimpleNamespace(
            principal_id=principal_id, principal_type="USER", status="active"
        )
        return await call_next(request)

    app.include_router(
        create_v4_router(
            get_dao=lambda: dao,
            get_access_control=lambda: access_control,
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def _access(*, allowed: bool = True) -> MagicMock:
    access = MagicMock()
    access.check_principal_permission = AsyncMock(return_value=allowed)
    access.check_principal_session_access = AsyncMock(return_value=allowed)
    access.check_access = AsyncMock(return_value=allowed)
    access.check_scope_role = AsyncMock(return_value=allowed)
    access.check_dataset_permission = AsyncMock(return_value=allowed)
    access.grant_access = AsyncMock()
    access.grant_principal_session_access = AsyncMock()
    return access


def test_v4_insert_creates_canonical_mutation_after_authorized_admission() -> None:
    dao = MagicMock()
    dao.admit_raw_log = AsyncMock(return_value={"log_id": 71})
    dao.record_mutation = AsyncMock()
    dao.create_v4_source_chunk = AsyncMock()
    dao.get_v4_session = AsyncMock(
        return_value={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_ids": ["dataset-a"],
            "agent_id": "agent-a",
            "session_id": "session-a",
            "status": "ACTIVE",
        }
    )
    client = _app(dao, _access())

    response = client.post(
        "/v4/memory/insert",
        json={
            "session_id": "session-a",
            "dataset_id": "dataset-a",
            "document_id": "document-a",
            "revision_id": "revision-a",
            "chunk_id": "chunk-a",
            "title": "Document A",
            "source_ref": "source-a",
            "content": "Exact content for the durable V4 candidate.",
            "metadata": {"jurisdiction": "TR"},
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["raw_log_id"] == 71
    (persisted,) = dao.record_mutation.await_args.args
    assert persisted["mutation_id"] == body["mutation_id"]
    assert persisted["candidate_id"] == body["candidate_id"]
    assert persisted["tenant_id"] == "tenant-a"
    assert persisted["dataset_id"] == "dataset-a"
    assert persisted["content_payload"] == "Exact content for the durable V4 candidate."
    assert dao.record_mutation.await_args.kwargs == {"raw_log_id": 71}


def test_v4_catalog_document_creation_is_dataset_authorized() -> None:
    dao = MagicMock()
    dao.create_v4_document = AsyncMock(
        return_value={
            "tenant_id": "tenant-a",
            "dataset_id": "dataset-a",
            "document_id": "document-a",
            "title": "Kanun",
        }
    )
    access = _access()
    response = _app(dao, access).post(
        "/v4/catalog/documents",
        json={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_id": "dataset-a",
            "document_id": "document-a",
            "title": "Kanun",
        },
    )

    assert response.status_code == 201
    access.check_scope_role.assert_awaited_once_with(
        "principal-a",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        dataset_id="dataset-a",
        required_role="WRITER",
    )
    dao.create_v4_document.assert_awaited_once_with(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        document_id="document-a",
        title="Kanun",
        external_ref=None,
    )


def test_v4_mutation_status_rejects_principal_without_owner_session_access() -> None:
    dao = MagicMock()
    dao.get_mutation_summary = AsyncMock(
        return_value={
            "mutation_id": "mutation-a",
            "candidate_id": "candidate-a",
            "agent_id": "agent-a",
            "dataset_id": "dataset-a",
            "session_id": "session-a",
            "pipeline_run_id": "pipeline-a",
            "state": "VALIDATED",
            "failure_class": None,
            "artifacts": [],
            "projections": [],
        }
    )
    dao.get_v4_session = AsyncMock(
        return_value={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_ids": ["dataset-a"],
            "agent_id": "agent-a",
            "session_id": "session-a",
            "status": "ACTIVE",
        }
    )
    dao.get_pipeline_run = AsyncMock()
    access = _access(allowed=False)
    response = _app(dao, access, principal_id="principal-b").get(
        "/v4/mutations/mutation-a"
    )

    assert response.status_code == 403
    access.check_principal_session_access.assert_awaited_once_with(
        "principal-b", "agent-a", "session-a", "READ"
    )


def test_v4_session_start_binds_server_generated_session_to_principal() -> None:
    access = _access()
    dao = MagicMock()
    dao.create_v4_session = AsyncMock(
        return_value={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_ids": ["dataset-a"],
            "agent_id": "agent-a",
            "principal_id": "principal-a",
            "session_id": "sess_generated",
            "status": "ACTIVE",
        }
    )
    response = _app(dao, access).post(
        "/v4/sessions/start",
        json={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_ids": ["dataset-a"],
            "agent_id": "agent-a",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["session_id"] == "sess_generated"
    access.check_principal_permission.assert_awaited_once_with(
        "principal-a", "agent-a", "SESSION_CREATE"
    )
    access.grant_access.assert_awaited_once_with("agent-a", body["session_id"], "WRITE")
    access.grant_principal_session_access.assert_awaited_once_with(
        "principal-a", "agent-a", body["session_id"], "WRITE"
    )


def test_v4_catalog_search_mutation_and_session_lifecycle_contracts() -> None:
    session = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "dataset_ids": ["dataset-a"],
        "agent_id": "agent-a",
        "session_id": "session-a",
        "status": "ACTIVE",
    }
    mutation = {
        "mutation_id": "mutation-a",
        "candidate_id": "candidate-a",
        "agent_id": "agent-a",
        "dataset_id": "dataset-a",
        "session_id": "session-a",
        "pipeline_run_id": "pipeline-a",
        "state": "COMMITTED",
        "failure_class": None,
        "artifacts": [{"artifact_id": "artifact-a"}],
        "projections": [{"projection_name": "SQL", "state": "APPLIED"}],
    }
    dao = MagicMock()
    dao.create_v4_workspace = AsyncMock(
        return_value={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "name": "Workspace A",
        }
    )
    dao.list_v4_workspaces = AsyncMock(
        return_value=[{"workspace_id": "workspace-a", "name": "Workspace A"}]
    )
    dao.ensure_v4_catalog_scope = AsyncMock()
    dao.list_v4_datasets = AsyncMock(
        return_value=[{"dataset_id": "dataset-a", "name": "Dataset A"}]
    )
    dao.list_v4_documents = AsyncMock(
        return_value=[{"document_id": "document-a", "title": "Document A"}]
    )
    dao.create_v4_revision = AsyncMock(
        return_value={"revision_id": "revision-a", "revision_number": 1}
    )
    dao.list_v4_revisions = AsyncMock(
        return_value=[{"revision_id": "revision-a", "revision_number": 1}]
    )
    dao.create_v4_source_chunk = AsyncMock(
        return_value={"chunk_id": "chunk-a", "content_payload": "Exact content"}
    )
    dao.purge_v4_document = AsyncMock(
        return_value={"document_id": "document-a", "status": "PURGE_PENDING"}
    )
    dao.get_v4_session = AsyncMock(return_value=session)
    dao.search_v4_memory = AsyncMock(
        return_value=[{"artifact_id": "artifact-a", "content": "Exact content"}]
    )
    dao.get_mutation_summary = AsyncMock(return_value=mutation)
    dao.get_pipeline_run = AsyncMock(
        return_value={"pipeline_run_id": "pipeline-a", "state": "COMMITTED"}
    )
    dao.request_pipeline_rollback = AsyncMock(
        return_value={"pipeline_run_id": "pipeline-a", "state": "ROLLING_BACK"}
    )
    dao.replay_pipeline_run = AsyncMock(
        return_value={"pipeline_run_id": "pipeline-a", "state": "QUEUED"}
    )
    dao.get_recent_logs = AsyncMock(
        return_value=[{"content": "First"}, {"content": ""}, {"content": "Second"}]
    )
    dao.list_session_mutation_summaries = AsyncMock(return_value=[mutation])
    dao.request_session_finalization = AsyncMock(
        return_value={"finalization_id": "finalization-a", "state": "PENDING"}
    )
    dao.end_v4_session = AsyncMock(return_value=True)
    client = _app(dao, _access())

    workspace = client.post(
        "/v4/catalog/workspaces",
        json={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "workspace_name": "Workspace A",
        },
    )
    assert workspace.status_code == 201
    assert client.get(
        "/v4/catalog/workspaces", params={"tenant_id": "tenant-a"}
    ).json()["workspaces"] == [{"workspace_id": "workspace-a", "name": "Workspace A"}]

    dataset = client.post(
        "/v4/catalog/datasets",
        json={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_id": "dataset-a",
            "dataset_name": "Dataset A",
        },
    )
    assert dataset.status_code == 201
    assert (
        client.get(
            "/v4/catalog/datasets",
            params={"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
        ).json()["datasets"][0]["dataset_id"]
        == "dataset-a"
    )
    assert (
        client.get(
            "/v4/catalog/documents",
            params={
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "dataset_id": "dataset-a",
            },
        ).json()["documents"][0]["document_id"]
        == "document-a"
    )

    revision = client.post(
        "/v4/catalog/revisions",
        json={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_id": "dataset-a",
            "document_id": "document-a",
            "revision_id": "revision-a",
            "revision_number": 1,
            "content_sha256": "0" * 64,
        },
    )
    assert revision.status_code == 201
    assert (
        client.get(
            "/v4/catalog/revisions",
            params={
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "dataset_id": "dataset-a",
                "document_id": "document-a",
            },
        ).json()["revisions"][0]["revision_id"]
        == "revision-a"
    )
    chunk = client.post(
        "/v4/catalog/source-chunks",
        json={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_id": "dataset-a",
            "document_id": "document-a",
            "revision_id": "revision-a",
            "chunk_id": "chunk-a",
            "title": "Document A",
            "content": "Exact content",
            "source_ref": "source-a",
        },
    )
    assert chunk.status_code == 201

    search = client.post(
        "/v4/memory/search",
        json={
            "session_id": "session-a",
            "dataset_ids": ["dataset-a"],
            "query": "Exact",
            "jurisdiction": "TR",
        },
    )
    assert search.status_code == 200
    assert search.json()["results"][0]["artifact_id"] == "artifact-a"
    dao.search_v4_memory.assert_awaited_once_with(
        tenant_id="tenant-a",
        agent_id="agent-a",
        dataset_ids=["dataset-a"],
        query="Exact",
        limit=10,
        jurisdiction="TR",
        valid_at=None,
    )

    status = client.get("/v4/mutations/mutation-a")
    assert status.status_code == 200
    assert status.json()["pipeline_run"]["state"] == "COMMITTED"
    assert (
        client.post("/v4/mutations/mutation-a/rollback").json()["state"]
        == "ROLLING_BACK"
    )
    assert client.post("/v4/mutations/mutation-a/replay").json()["state"] == "QUEUED"

    context = client.get("/v4/sessions/session-a/context")
    assert context.status_code == 200
    assert context.json()["context"] == "First\nSecond"
    ended = client.post("/v4/sessions/session-a/end")
    assert ended.status_code == 200
    assert ended.json() == {
        "status": "pending",
        "session_id": "session-a",
        "finalization_id": "finalization-a",
    }
    purged = client.delete(
        "/v4/catalog/documents/document-a",
        params={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_id": "dataset-a",
        },
    )
    assert purged.status_code == 202
    assert purged.json()["status"] == "PURGE_PENDING"


def test_v4_insert_maps_durable_queue_admission_failures() -> None:
    session = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "dataset_ids": ["dataset-a"],
        "agent_id": "agent-a",
        "session_id": "session-a",
        "status": "ACTIVE",
    }
    payload = {
        "session_id": "session-a",
        "dataset_id": "dataset-a",
        "document_id": "document-a",
        "revision_id": "revision-a",
        "chunk_id": "chunk-a",
        "title": "Document A",
        "source_ref": "source-a",
        "content": "Exact content",
    }
    expected = (
        (QueueRecordTooLargeError(), 413, "queue_record_too_large"),
        (QueueOverCapacityError("tenant"), 503, "queue_over_capacity"),
        (QueueUnavailableError(), 503, "queue_unavailable"),
    )
    for error, status_code, detail in expected:
        dao = MagicMock()
        dao.get_v4_session = AsyncMock(return_value=session)
        dao.create_v4_source_chunk = AsyncMock()
        dao.admit_raw_log = AsyncMock(side_effect=error)
        response = _app(dao, _access()).post("/v4/memory/insert", json=payload)
        assert response.status_code == status_code
        assert response.json() == {"detail": detail}


def test_v4_session_scope_and_mutation_control_fail_closed() -> None:
    search_payload = {
        "session_id": "session-a",
        "query": "Exact",
    }
    dao = MagicMock()
    dao.get_v4_session = AsyncMock(return_value=None)
    unknown = _app(dao, _access()).post("/v4/memory/search", json=search_payload)
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "Unknown session"}

    session = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "dataset_ids": ["dataset-a"],
        "agent_id": "agent-a",
        "session_id": "session-a",
        "status": "ACTIVE",
    }
    dao.get_v4_session = AsyncMock(return_value=session)
    outside_scope = _app(dao, _access()).post(
        "/v4/memory/search",
        json={**search_payload, "dataset_ids": ["dataset-b"]},
    )
    assert outside_scope.status_code == 403
    assert outside_scope.json() == {"detail": "Dataset is outside session scope"}

    dao.get_mutation_summary = AsyncMock(return_value=None)
    client = _app(dao, _access())
    assert client.get("/v4/mutations/missing").status_code == 404
    assert client.post("/v4/mutations/missing/rollback").status_code == 404
    assert client.post("/v4/mutations/missing/replay").status_code == 404

    mutation = {
        "mutation_id": "mutation-a",
        "candidate_id": "candidate-a",
        "dataset_id": "dataset-a",
        "session_id": "session-a",
        "pipeline_run_id": "pipeline-a",
        "state": "VALIDATED",
        "failure_class": None,
        "artifacts": [],
        "projections": [],
    }
    dao.get_mutation_summary = AsyncMock(return_value=mutation)
    dao.get_v4_session = AsyncMock(return_value={**session, "status": "ENDED"})
    closed = _app(dao, _access()).post("/v4/mutations/mutation-a/rollback")
    assert closed.status_code == 409
    assert closed.json() == {"detail": "Session is not active"}

    dao.get_v4_session = AsyncMock(return_value=session)
    denied = _access()
    denied.check_dataset_permission = AsyncMock(return_value=False)
    client = _app(dao, denied)
    rollback = client.post("/v4/mutations/mutation-a/rollback")
    replay = client.post("/v4/mutations/mutation-a/replay")
    assert rollback.status_code == 403
    assert rollback.json() == {"detail": "ROLLBACK permission required"}
    assert replay.status_code == 403
    assert replay.json() == {"detail": "ROLLBACK permission required"}
