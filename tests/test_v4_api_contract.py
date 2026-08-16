"""V4 API admission and principal/session authorization contracts."""

from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from pydantic import ValidationError

import mesa_api.v4_router as v4_api
from mesa_api.v4_router import V4MemoryInsertRequest, create_v4_router
from mesa_memory.consolidation.policy import SingleLLMValidationPolicy
from mesa_storage.dao import (
    NonHeadRollbackConflictError,
    QueueOverCapacityError,
    QueueRecordTooLargeError,
    QueueUnavailableError,
)
from mesa_storage.repositories.operations import (
    OperationIdempotencyConflictError,
    OperationNotFoundError,
    OperationStateError,
)


def _app(
    dao,
    access_control,
    *,
    principal_id: str = "principal-a",
    maintenance_pending: bool = False,
) -> FastAPI:  # type: ignore[no-untyped-def]
    async def attach_principal(request: Request) -> None:
        request.state.principal = SimpleNamespace(
            principal_id=principal_id, principal_type="USER", status="active"
        )

    async def get_dao():  # type: ignore[no-untyped-def]
        return dao

    async def get_access_control():  # type: ignore[no-untyped-def]
        return access_control

    dao.rebuild_admission.is_pending = AsyncMock(return_value=maintenance_pending)

    app = FastAPI(dependencies=[Depends(attach_principal)])

    app.include_router(
        create_v4_router(
            get_dao=get_dao,
            get_access_control=get_access_control,
        )
    )
    return app


ClientFactory = Callable[[FastAPI], httpx.AsyncClient]


@pytest.fixture
async def asgi_client() -> AsyncIterator[ClientFactory]:
    clients: list[httpx.AsyncClient] = []

    def create(app: FastAPI) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        )
        clients.append(client)
        return client

    yield create

    for client in clients:
        await client.aclose()


def _access(*, allowed: bool = True) -> MagicMock:
    access = MagicMock()
    access.check_principal_permission = AsyncMock(return_value=allowed)
    access.check_principal_session_access = AsyncMock(return_value=allowed)
    access.check_access = AsyncMock(return_value=allowed)
    access.check_scope_role = AsyncMock(return_value=allowed)
    access.check_dataset_permission = AsyncMock(return_value=allowed)
    access.check_control_role = AsyncMock(return_value=allowed)
    access.grant_access = AsyncMock()
    access.grant_principal_session_access = AsyncMock()
    return access


def _operation(*, state: str = "PENDING") -> dict:
    return {
        "operation_id": "operation-a",
        "operation_kind": "PROJECTION_REBUILD",
        "scope_kind": "STORAGE_ROOT",
        "scope_key": "default",
        "requested_by_principal_id": "principal-a",
        "idempotency_key": "rebuild-a",
        "payload_hash": "a" * 64,
        "state": state,
        "claimed_by": None,
        "claim_token": None,
        "fencing_token": 0,
        "lease_expires_at": None,
        "attempt_count": 1,
        "retry_limit": 3,
        "progress_completed": 2,
        "progress_total": 5,
        "checkpoint": {"last_chunk": "content-bearing-value"},
        "source_manifest_hash": "b" * 64,
        "source_manifest": {"physical_path": "/private/storage"},
        "source_generation_id": "legacy",
        "target_generation_id": "generation-a",
        "last_error_class": (
            "ProviderUnavailable" if state == "RETRYABLE_FAILED" else None
        ),
        "last_error_code": "provider-secret-code",
        "created_at": "2026-08-03 12:00:00",
        "updated_at": "2026-08-03 12:01:00",
        "completed_at": None,
    }


def test_v4_insert_schema_rejects_secret_and_excessive_metadata() -> None:
    payload = {
        "session_id": "session-a",
        "dataset_id": "dataset-a",
        "document_id": "document-a",
        "revision_id": "revision-a",
        "chunk_id": "chunk-a",
        "title": "Document A",
        "source_ref": "source-a",
        "content": "safe content",
    }
    with pytest.raises(ValidationError, match="secret"):
        V4MemoryInsertRequest(**(payload | {"content": "password=not-for-storage"}))
    with pytest.raises(ValidationError, match="metadata exceeds"):
        V4MemoryInsertRequest(**(payload | {"metadata": {"x": "a" * (16 * 1024)}}))


@pytest.mark.asyncio
async def test_v4_capability_reports_only_enabled_specific_behaviours(
    asgi_client: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(v4_api.config, "v4_rebuild_enabled", False)
    available_dao = MagicMock()
    available_dao.canonical_v4_writes_enabled = True
    client = asgi_client(_app(available_dao, _access()))

    disabled = (await client.get("/v4/capability")).json()

    assert disabled == {
        "api_version": "v4",
        "features": [
            "canonical_ledger",
            "projection_outbox",
            "idempotent_ingestion",
            "lexical_retrieval",
            "assertion_relational_lane",
            "validity_interval_filtering",
            "graph_projection",
        ],
        "capabilities": {
            "canonical_ledger": True,
            "projection_outbox": True,
            "idempotent_ingestion": True,
            "vector_retrieval": False,
            "lexical_retrieval": True,
            "assertion_relational_lane": True,
            "validity_interval_filtering": True,
            "graph_projection": True,
            "graph_neighbor_retrieval": False,
            "associative_ppr": False,
            "bitemporal_query": False,
            "durable_rebuild": False,
            "human_review": False,
        },
        "validation": {
            "mode": 0,
            "policy": "deterministic_only",
            "llm_validation_enabled": False,
            "validator_count": 0,
        },
        "limits": {
            "rebuild_kind": "projection",
            "rebuild_scope": "storage_root",
            "requires_offline_runner": True,
        },
    }
    assert "graph_retrieval" not in disabled["features"]
    assert "temporal_filtering" not in disabled["features"]

    monkeypatch.setattr(v4_api.config, "v4_rebuild_enabled", True)
    enabled = (await client.get("/v4/capability")).json()

    assert enabled["capabilities"]["durable_rebuild"] is True
    assert "durable_rebuild" in enabled["features"]

    unavailable_dao = MagicMock()
    unavailable_dao.canonical_v4_writes_enabled = False
    unavailable = (
        await asgi_client(_app(unavailable_dao, _access())).get("/v4/capability")
    ).json()
    assert unavailable["capabilities"]["idempotent_ingestion"] is False
    assert unavailable["capabilities"]["projection_outbox"] is False
    assert unavailable["capabilities"]["graph_projection"] is False


@pytest.mark.asyncio
async def test_v4_capability_reports_the_composed_validation_policy(
    asgi_client: ClientFactory,
) -> None:
    dao = MagicMock()
    dao.canonical_v4_writes_enabled = True
    policy = SingleLLMValidationPolicy(MagicMock())

    async def attach_principal(request: Request) -> None:
        request.state.principal = SimpleNamespace(
            principal_id="principal-a", principal_type="USER", status="active"
        )

    async def get_dao():  # type: ignore[no-untyped-def]
        return dao

    app = FastAPI(dependencies=[Depends(attach_principal)])
    app.include_router(
        create_v4_router(
            get_dao=get_dao,
            get_access_control=_access(),
            get_composed_validation_policy=lambda: policy,
        )
    )
    response = await asgi_client(app).get("/v4/capability")

    assert response.status_code == 200
    assert response.json()["validation"] == {
        "mode": 1,
        "policy": "single_llm",
        "llm_validation_enabled": True,
        "validator_count": 1,
    }


@pytest.mark.asyncio
async def test_v4_insert_creates_canonical_mutation_after_authorized_admission(
    asgi_client: ClientFactory,
) -> None:
    dao = MagicMock()
    dao.admit_v4_memory = AsyncMock(
        return_value={
            "outcome": "ADMITTED",
            "response": {
                "status": "accepted",
                "mutation_id": "mutation-a",
                "candidate_id": "candidate-a",
                "pipeline_run_id": "pipeline-a",
                "raw_log_id": 71,
            },
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
    client = asgi_client(_app(dao, _access()))

    response = await client.post(
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
    assert body["mutation_id"] == "mutation-a"
    dao.admit_v4_memory.assert_awaited_once()
    admission = dao.admit_v4_memory.await_args.kwargs
    assert admission["tenant_id"] == "tenant-a"
    assert admission["dataset_id"] == "dataset-a"
    assert admission["content_payload"] == "Exact content for the durable V4 candidate."
    assert admission["embedding_provider"] == "sentence-transformers"
    assert admission["embedding_model"]
    assert admission["embedding_version"] == "v1"
    assert admission["embedding_dimension"] > 0


@pytest.mark.asyncio
async def test_v4_catalog_document_creation_is_dataset_authorized(
    asgi_client: ClientFactory,
) -> None:
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
    response = await asgi_client(_app(dao, access)).post(
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


@pytest.mark.asyncio
async def test_v4_mutation_status_rejects_principal_without_owner_session_access(
    asgi_client: ClientFactory,
) -> None:
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
    response = await asgi_client(_app(dao, access, principal_id="principal-b")).get(
        "/v4/mutations/mutation-a",
    )

    assert response.status_code == 403
    access.check_principal_session_access.assert_awaited_once_with(
        "principal-b", "agent-a", "session-a", "READ"
    )


@pytest.mark.asyncio
async def test_v4_session_start_binds_server_generated_session_to_principal(
    asgi_client: ClientFactory,
) -> None:
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
    response = await asgi_client(_app(dao, access)).post(
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


@pytest.mark.asyncio
async def test_v4_rebuild_submit_requires_admin_flag_and_idempotency_key(
    asgi_client: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dao = MagicMock()
    dao.operations.submit = AsyncMock(return_value=_operation())
    denied_access = _access(allowed=False)

    async def get_unauthenticated_dao():
        return dao

    async def get_unauthenticated_access():
        return _access()

    unauthenticated_app = FastAPI()
    unauthenticated_app.include_router(
        create_v4_router(
            get_dao=get_unauthenticated_dao,
            get_access_control=get_unauthenticated_access,
        )
    )
    headers = {"Idempotency-Key": "rebuild-a"}
    monkeypatch.setattr(v4_api.config, "v4_rebuild_enabled", True)

    denied = await asgi_client(unauthenticated_app).post(
        "/v4/operations/rebuild", headers=headers
    )
    forbidden = await asgi_client(_app(dao, denied_access)).post(
        "/v4/operations/rebuild", headers=headers
    )
    missing_key = await asgi_client(_app(dao, _access())).post("/v4/operations/rebuild")
    monkeypatch.setattr(v4_api.config, "v4_rebuild_enabled", False)
    disabled = await asgi_client(_app(dao, _access())).post(
        "/v4/operations/rebuild", headers=headers
    )

    assert denied.status_code == 401
    assert forbidden.status_code == 403
    assert missing_key.status_code == 422
    assert disabled.status_code == 501
    dao.operations.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_v4_rebuild_submit_commits_before_content_free_202(
    asgi_client: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dao = MagicMock()
    dao.operations.submit = AsyncMock(return_value=_operation())
    access = _access()
    monkeypatch.setattr(v4_api.config, "v4_rebuild_enabled", True)

    response = await asgi_client(_app(dao, access)).post(
        "/v4/operations/rebuild",
        headers={"Idempotency-Key": "rebuild-a"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "operation_id": "operation-a",
        "operation_kind": "projection_rebuild",
        "scope": "storage_root",
        "state": "PENDING",
        "attempt": 1,
        "progress": {"completed": 2, "total": 5},
        "error_class": None,
        "retry_available": False,
        "cancel_available": True,
        "created_at": "2026-08-03 12:00:00",
        "updated_at": "2026-08-03 12:01:00",
        "completed_at": None,
    }
    access.check_control_role.assert_awaited_once_with("principal-a", "ADMIN")
    submitted = dao.operations.submit.await_args.kwargs
    assert submitted["requested_by_principal_id"] == "principal-a"
    assert submitted["idempotency_key"] == "rebuild-a"
    assert len(submitted["payload_hash"]) == 64
    assert "checkpoint" not in response.text
    assert "physical_path" not in response.text
    assert "principal-a" not in response.text


@pytest.mark.asyncio
async def test_v4_rebuild_operation_controls_hide_existence_and_map_conflicts(
    asgi_client: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(v4_api.config, "v4_rebuild_enabled", True)
    dao = MagicMock()
    dao.operations.get = AsyncMock(return_value=_operation())
    dao.operations.cancel = AsyncMock(return_value=_operation(state="CANCELLED"))
    dao.operations.retry = AsyncMock(return_value=_operation(state="PENDING"))

    hidden = await asgi_client(_app(dao, _access(allowed=False))).get(
        "/v4/operations/operation-a"
    )
    assert hidden.status_code == 404
    dao.operations.get.assert_not_awaited()

    client = asgi_client(_app(dao, _access()))
    status = await client.get("/v4/operations/operation-a")
    cancelled = await client.post("/v4/operations/operation-a/cancel")
    retried = await client.post("/v4/operations/operation-a/retry")

    assert status.status_code == 200
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "CANCELLED"
    assert retried.status_code == 202

    dao.operations.get = AsyncMock(return_value=None)
    assert (await client.get("/v4/operations/missing")).status_code == 404
    dao.operations.cancel = AsyncMock(side_effect=OperationNotFoundError())
    assert (await client.post("/v4/operations/missing/cancel")).status_code == 404
    dao.operations.retry = AsyncMock(side_effect=OperationStateError())
    assert (await client.post("/v4/operations/operation-a/retry")).status_code == 409


@pytest.mark.asyncio
async def test_v4_rebuild_alias_rejects_scoped_requests_and_conflicting_keys(
    asgi_client: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(v4_api.config, "v4_rebuild_enabled", True)
    dao = MagicMock()
    dao.operations.submit = AsyncMock(return_value=_operation())
    client = asgi_client(_app(dao, _access()))
    headers = {"Idempotency-Key": "rebuild-a"}

    scoped = await client.post(
        "/v4/rebuild", params={"tenant_id": "tenant-a"}, headers=headers
    )
    root_wide = await client.post("/v4/rebuild", headers=headers)

    assert scoped.status_code == 409
    assert root_wide.status_code == 202
    dao.operations.submit.assert_awaited_once()

    dao.operations.submit = AsyncMock(side_effect=OperationIdempotencyConflictError())
    conflict = await client.post("/v4/operations/rebuild", headers=headers)
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_v4_rebuild_maintenance_gates_mutations_but_keeps_controls_open(
    asgi_client: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(v4_api.config, "v4_rebuild_enabled", True)
    dao = MagicMock()
    dao.create_v4_workspace = AsyncMock(
        return_value={"tenant_id": "tenant-a", "workspace_id": "workspace-a"}
    )
    dao.operations.get = AsyncMock(return_value=_operation())
    dao.operations.cancel = AsyncMock(return_value=_operation(state="CANCELLED"))
    client = asgi_client(_app(dao, _access(), maintenance_pending=True))
    payload = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
    }

    gated = await client.post("/v4/catalog/workspaces", json=payload)
    capability = await client.get("/v4/capability")
    status = await client.get("/v4/operations/operation-a")
    cancelled = await client.post("/v4/operations/operation-a/cancel")

    assert gated.status_code == 503
    assert gated.json() == {"detail": "maintenance_pending"}
    assert gated.headers["Retry-After"] == "5"
    dao.create_v4_workspace.assert_not_awaited()
    assert capability.status_code == 200
    assert status.status_code == 200
    assert cancelled.status_code == 200

    dao.rebuild_admission.is_pending.return_value = False
    reopened = await client.post("/v4/catalog/workspaces", json=payload)

    assert reopened.status_code == 201
    dao.create_v4_workspace.assert_awaited_once()


@pytest.mark.asyncio
async def test_v4_catalog_search_mutation_and_session_lifecycle_contracts(
    asgi_client: ClientFactory,
) -> None:
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
    client = asgi_client(_app(dao, _access()))

    workspace = await client.post(
        "/v4/catalog/workspaces",
        json={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "workspace_name": "Workspace A",
        },
    )
    assert workspace.status_code == 201
    assert (
        await client.get("/v4/catalog/workspaces", params={"tenant_id": "tenant-a"})
    ).json()["workspaces"] == [{"workspace_id": "workspace-a", "name": "Workspace A"}]

    dataset = await client.post(
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
        await client.get(
            "/v4/catalog/datasets",
            params={"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
        )
    ).json()["datasets"][0]["dataset_id"] == "dataset-a"
    assert (
        await client.get(
            "/v4/catalog/documents",
            params={
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "dataset_id": "dataset-a",
            },
        )
    ).json()["documents"][0]["document_id"] == "document-a"

    revision = await client.post(
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
        await client.get(
            "/v4/catalog/revisions",
            params={
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "dataset_id": "dataset-a",
                "document_id": "document-a",
            },
        )
    ).json()["revisions"][0]["revision_id"] == "revision-a"
    chunk = await client.post(
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

    search = await client.post(
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
        valid_from=None,
        valid_to=None,
    )

    status = await client.get("/v4/mutations/mutation-a")
    assert status.status_code == 200
    assert status.json()["pipeline_run"]["state"] == "COMMITTED"
    assert (await client.post("/v4/mutations/mutation-a/rollback")).json()[
        "state"
    ] == "ROLLING_BACK"
    assert (await client.post("/v4/mutations/mutation-a/replay")).json()[
        "state"
    ] == "QUEUED"

    context = await client.get("/v4/sessions/session-a/context")
    assert context.status_code == 200
    context_body = context.json()
    assert "=== Current Session Information ===" in context_body["context"]
    assert "- First" in context_body["context"]
    assert "- Second" in context_body["context"]
    assert context_body["canonical_memories"] == [
        {"artifact_id": "artifact-a", "content": "Exact content"}
    ]
    ended = await client.post("/v4/sessions/session-a/end")
    assert ended.status_code == 200
    assert ended.json() == {
        "status": "pending",
        "session_id": "session-a",
        "finalization_id": "finalization-a",
    }
    purged = await client.delete(
        "/v4/catalog/documents/document-a",
        params={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_id": "dataset-a",
        },
    )
    assert purged.status_code == 202
    assert purged.json()["status"] == "PURGE_PENDING"


@pytest.mark.asyncio
async def test_v4_insert_maps_durable_queue_admission_failures(
    asgi_client: ClientFactory,
) -> None:
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
        dao.admit_v4_memory = AsyncMock(side_effect=error)
        response = await asgi_client(_app(dao, _access())).post(
            "/v4/memory/insert", json=payload
        )
        assert response.status_code == status_code
        assert response.json() == {"detail": detail}


@pytest.mark.asyncio
async def test_v4_session_scope_and_mutation_control_fail_closed(
    asgi_client: ClientFactory,
) -> None:
    search_payload = {
        "session_id": "session-a",
        "query": "Exact",
    }
    dao = MagicMock()
    dao.get_v4_session = AsyncMock(return_value=None)
    unknown = await asgi_client(_app(dao, _access())).post(
        "/v4/memory/search", json=search_payload
    )
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
    outside_scope = await asgi_client(_app(dao, _access())).post(
        "/v4/memory/search",
        json={**search_payload, "dataset_ids": ["dataset-b"]},
    )
    assert outside_scope.status_code == 403
    assert outside_scope.json() == {"detail": "Dataset is outside session scope"}

    dao.get_mutation_summary = AsyncMock(return_value=None)
    client = asgi_client(_app(dao, _access()))
    assert (await client.get("/v4/mutations/missing")).status_code == 404
    assert (await client.post("/v4/mutations/missing/rollback")).status_code == 404
    assert (await client.post("/v4/mutations/missing/replay")).status_code == 404

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
    closed = await asgi_client(_app(dao, _access())).post(
        "/v4/mutations/mutation-a/rollback"
    )
    assert closed.status_code == 409
    assert closed.json() == {"detail": "Session is not active"}

    dao.get_v4_session = AsyncMock(return_value=session)
    denied = _access()
    denied.check_dataset_permission = AsyncMock(return_value=False)
    client = asgi_client(_app(dao, denied))
    rollback = await client.post("/v4/mutations/mutation-a/rollback")
    replay = await client.post("/v4/mutations/mutation-a/replay")
    assert rollback.status_code == 403
    assert rollback.json() == {"detail": "ROLLBACK permission required"}
    assert replay.status_code == 403
    assert replay.json() == {"detail": "ROLLBACK permission required"}


@pytest.mark.asyncio
async def test_v4_non_head_rollback_returns_typed_conflict(
    asgi_client: ClientFactory,
) -> None:
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
        "dataset_id": "dataset-a",
        "session_id": "session-a",
        "pipeline_run_id": "pipeline-a",
        "state": "COMMITTED",
        "failure_class": None,
        "artifacts": [],
        "projections": [],
    }
    dao = MagicMock()
    dao.get_v4_session = AsyncMock(return_value=session)
    dao.get_mutation_summary = AsyncMock(return_value=mutation)
    dao.request_pipeline_rollback = AsyncMock(
        side_effect=NonHeadRollbackConflictError("non-head")
    )
    response = await asgi_client(_app(dao, _access())).post(
        "/v4/mutations/mutation-a/rollback"
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "NON_HEAD_ROLLBACK_CONFLICT"}
