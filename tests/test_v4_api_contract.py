"""V4 API admission and principal/session authorization contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mesa_api.v4_router import create_v4_router


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
    persisted, = dao.record_mutation.await_args.args
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
