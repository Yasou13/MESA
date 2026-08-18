# mypy: disable-error-code="no-untyped-def,untyped-decorator,no-any-return"
"""Versioned V4 full-cognitive API contract.

V3 remains the lexical-core compatibility surface.  V4 admission creates a
canonical mutation before background processing and every later operation is
authorized against a verified principal-to-agent-to-session binding.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mesa_api.admission import require_mutation_admission as _require_mutation_admission
from mesa_memory.config import config, configured_embedding_identity
from mesa_memory.consolidation.policy import ValidationPolicy
from mesa_memory.context_builder import ContextBuilder
from mesa_memory.security.input_validation import validate_write_payload
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import (
    MemoryDAO,
    NonHeadRollbackConflictError,
    NonReplayableMutationConflictError,
    PurgeRetryPendingError,
    QueueOverCapacityError,
    QueueRecordTooLargeError,
    QueueUnavailableError,
)
from mesa_storage.repositories.operations import (
    OperationActiveConflictError,
    OperationIdempotencyConflictError,
    OperationNotFoundError,
    OperationStateError,
)

logger = logging.getLogger("MESA_V4_API")


class V4MutationStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    mutation_id: str
    candidate_id: str
    state: str
    failure_class: str | None = None
    rejection_reason: str | None = None
    tier3_audit: dict | None = None
    pipeline_run: dict | None = None
    artifacts: list[dict] = Field(default_factory=list)
    projections: list[dict] = Field(default_factory=list)


class V4CapabilityFlags(BaseModel):
    model_config = ConfigDict(frozen=True)

    canonical_ledger: bool = True
    projection_outbox: bool = True
    idempotent_ingestion: bool = True
    vector_retrieval: bool = True
    lexical_retrieval: bool = True
    assertion_relational_lane: bool = True
    validity_interval_filtering: bool = True
    graph_projection: bool = True
    graph_neighbor_retrieval: bool = False
    associative_ppr: bool = False
    bitemporal_query: bool = False
    durable_rebuild: bool = False
    human_review: bool = False


class V4ValidationCapability(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: int = 0
    policy: str = "deterministic_only"
    llm_validation_enabled: bool = False
    validator_count: int = 0


class V4CapabilityLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    rebuild_kind: str = "projection"
    rebuild_scope: str = "storage_root"
    requires_offline_runner: bool = True


class V4CapabilityResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    api_version: str = "v4"
    features: list[str]
    capabilities: V4CapabilityFlags
    validation: V4ValidationCapability = Field(default_factory=V4ValidationCapability)
    limits: V4CapabilityLimits = Field(default_factory=V4CapabilityLimits)


class V4OperationProgress(BaseModel):
    model_config = ConfigDict(frozen=True)

    completed: int = Field(ge=0)
    total: int = Field(ge=0)


class V4OperationResponse(BaseModel):
    """Content-free control-plane view of one durable operation."""

    model_config = ConfigDict(frozen=True)

    operation_id: str
    operation_kind: str = "projection_rebuild"
    scope: str = "storage_root"
    state: str
    attempt: int = Field(ge=0)
    progress: V4OperationProgress
    error_class: str | None = None
    retry_available: bool
    cancel_available: bool
    created_at: str
    updated_at: str
    completed_at: str | None = None


class V4DatasetRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    tenant_name: str | None = Field(default=None, max_length=256)
    workspace_name: str | None = Field(default=None, max_length=256)
    dataset_name: str | None = Field(default=None, max_length=256)


class V4WorkspaceRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    tenant_name: str | None = Field(default=None, max_length=256)
    workspace_name: str | None = Field(default=None, max_length=256)


class V4DocumentRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    document_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)
    external_ref: str | None = Field(default=None, max_length=2048)


class V4RevisionRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    document_id: str = Field(min_length=1, max_length=256)
    revision_id: str = Field(min_length=1, max_length=256)
    revision_number: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    supersedes_revision_id: str | None = Field(default=None, max_length=256)


class V4SourceChunkRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    document_id: str = Field(min_length=1, max_length=256)
    revision_id: str = Field(min_length=1, max_length=256)
    chunk_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1, max_length=32768)
    source_ref: str = Field(min_length=1, max_length=2048)
    revision_number: int = Field(default=1, ge=1)
    chunk_ordinal: int = Field(default=0, ge=0)
    finalize_revision: bool = True
    external_ref: str | None = Field(default=None, max_length=2048)
    supersedes_revision_id: str | None = Field(default=None, max_length=256)


class V4SessionStartRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    dataset_ids: list[str] = Field(min_length=1, max_length=64)
    agent_id: str = Field(min_length=1, max_length=128)


class V4MemoryInsertRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    session_id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    document_id: str = Field(min_length=1, max_length=256)
    revision_id: str = Field(min_length=1, max_length=256)
    chunk_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)
    source_ref: str = Field(min_length=1, max_length=2048)
    content: str = Field(min_length=1, max_length=32768)
    evidence_span: str = Field(default="", max_length=4096)
    revision_number: int = Field(default=1, ge=1)
    chunk_ordinal: int = Field(default=0, ge=0)
    finalize_revision: bool = True
    supersedes_revision_id: str | None = Field(default=None, max_length=256)
    metadata: dict = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_write_boundary(self) -> "V4MemoryInsertRequest":
        validate_write_payload(self.content, self.metadata)
        return self


class V4SearchRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    session_id: str = Field(min_length=1, max_length=128)
    dataset_ids: list[str] | None = Field(default=None, max_length=64)
    query: str = Field(min_length=1, max_length=4096)
    limit: int = Field(default=10, ge=1, le=50)
    jurisdiction: str | None = Field(default=None, max_length=256)
    valid_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @model_validator(mode="after")
    def validate_temporal_range(self) -> "V4SearchRequest":
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from must not be after valid_to")
        return self


def _active_principal(request: Request):
    principal = getattr(request.state, "principal", None)
    if principal is None or getattr(principal, "status", None) != "active":
        raise HTTPException(
            status_code=401, detail="Active authenticated principal required"
        )
    return principal


async def _require_control_admin(
    request: Request,
    access_control: AccessControl,
    *,
    hide_existence: bool = False,
):
    principal = _active_principal(request)
    if not await access_control.check_control_role(
        str(principal.principal_id), "ADMIN"
    ):
        if hide_existence:
            raise HTTPException(status_code=404, detail="Operation not found")
        raise HTTPException(
            status_code=403, detail="Control administrator role required"
        )
    return principal


def _public_operation(operation: dict) -> V4OperationResponse:
    """Whitelist bounded operation fields safe to expose outside storage."""
    state = str(operation["state"])
    return V4OperationResponse(
        operation_id=str(operation["operation_id"]),
        state=state,
        attempt=int(operation["attempt_count"]),
        progress=V4OperationProgress(
            completed=int(operation["progress_completed"]),
            total=int(operation["progress_total"]),
        ),
        error_class=(
            str(operation["last_error_class"])
            if operation.get("last_error_class") is not None
            else None
        ),
        retry_available=state == "RETRYABLE_FAILED",
        cancel_available=state in {"PENDING", "RETRYABLE_FAILED"},
        created_at=str(operation["created_at"]),
        updated_at=str(operation["updated_at"]),
        completed_at=(
            str(operation["completed_at"])
            if operation.get("completed_at") is not None
            else None
        ),
    )


async def _require_session_access(
    request: Request,
    access_control: AccessControl,
    *,
    agent_id: str,
    session_id: str,
    level: str,
    allow_closed: bool = False,
) -> None:
    principal = _active_principal(request)
    if not await access_control.check_principal_session_access(
        principal.principal_id, agent_id, session_id, level
    ):
        raise HTTPException(status_code=403, detail="Principal/session access denied")
    if not allow_closed and not await access_control.check_access(
        agent_id, session_id, level
    ):
        raise HTTPException(
            status_code=403, detail="Session is not active for requested operation"
        )


async def _require_dataset_roles(
    request: Request,
    access_control: AccessControl,
    *,
    tenant_id: str,
    workspace_id: str,
    dataset_ids: list[str],
    required_role: str,
) -> None:
    principal = _active_principal(request)
    for dataset_id in sorted(set(dataset_ids)):
        if not await access_control.check_scope_role(
            principal.principal_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            required_role=required_role,
        ):
            raise HTTPException(
                status_code=403,
                detail="Principal lacks required dataset role",
            )


async def _authorized_v4_session(
    request: Request,
    dao: MemoryDAO,
    access_control: AccessControl,
    session_id: str,
    *,
    level: str,
    allow_closed: bool = False,
) -> dict:
    session = await dao.get_v4_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session")
    if not allow_closed and session["status"] != "ACTIVE" and level == "WRITE":
        raise HTTPException(status_code=409, detail="Session is not active")
    await _require_session_access(
        request,
        access_control,
        agent_id=str(session["agent_id"]),
        session_id=session_id,
        level=level,
        allow_closed=allow_closed,
    )
    await _require_dataset_roles(
        request,
        access_control,
        tenant_id=str(session["tenant_id"]),
        workspace_id=str(session["workspace_id"]),
        dataset_ids=list(session["dataset_ids"]),
        required_role="WRITER" if level == "WRITE" else "READER",
    )
    return session


def _require_historical_mutation_scope(
    mutation: dict, session: dict
) -> None:
    """Bind historical administration to the mutation's immutable session scope."""
    mutation_workspace = mutation.get("workspace_id")
    if (
        str(mutation.get("tenant_id") or "") != str(session["tenant_id"])
        or str(mutation.get("agent_id") or "") != str(session["agent_id"])
        or str(mutation.get("dataset_id") or "")
        not in {str(item) for item in session["dataset_ids"]}
        or (
            mutation_workspace
            and str(mutation_workspace) != str(session["workspace_id"])
        )
    ):
        raise HTTPException(
            status_code=403,
            detail="Mutation is outside historical session scope",
        )


def create_v4_router(
    get_dao: Callable[[], MemoryDAO],
    *,
    get_access_control: Callable[[], AccessControl],
    get_composed_validation_policy: Callable[[], ValidationPolicy | None] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/v4", tags=["v4-full-cognitive"])

    def current_validation_policy() -> ValidationPolicy | None:
        return (
            get_composed_validation_policy()
            if get_composed_validation_policy is not None
            else None
        )

    @router.post("/catalog/workspaces", status_code=201)
    async def create_workspace(
        request: Request,
        payload: V4WorkspaceRequest,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        await _require_dataset_roles(
            request,
            access_control,
            tenant_id=payload.tenant_id,
            workspace_id=payload.workspace_id,
            dataset_ids=[""],
            required_role="OWNER",
        )
        await _require_mutation_admission(dao)
        try:
            return await dao.create_v4_workspace(
                tenant_id=payload.tenant_id,
                workspace_id=payload.workspace_id,
                tenant_name=payload.tenant_name,
                workspace_name=payload.workspace_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/catalog/workspaces")
    async def list_workspaces(
        tenant_id: str,
        request: Request,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        workspaces = await dao.list_v4_workspaces(tenant_id=tenant_id)
        visible = []
        for workspace in workspaces:
            try:
                await _require_dataset_roles(
                    request,
                    access_control,
                    tenant_id=tenant_id,
                    workspace_id=str(workspace["workspace_id"]),
                    dataset_ids=[""],
                    required_role="READER",
                )
            except HTTPException:
                continue
            visible.append(workspace)
        return {"workspaces": visible}

    @router.post("/catalog/datasets", status_code=201)
    async def create_dataset(
        request: Request,
        payload: V4DatasetRequest,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        await _require_dataset_roles(
            request,
            access_control,
            tenant_id=payload.tenant_id,
            workspace_id=payload.workspace_id,
            dataset_ids=[payload.dataset_id],
            required_role="OWNER",
        )
        await _require_mutation_admission(dao)
        try:
            await dao.ensure_v4_catalog_scope(
                tenant_id=payload.tenant_id,
                workspace_id=payload.workspace_id,
                dataset_id=payload.dataset_id,
                tenant_name=payload.tenant_name,
                workspace_name=payload.workspace_name,
                dataset_name=payload.dataset_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return payload.model_dump()

    @router.get("/catalog/datasets")
    async def list_datasets(
        tenant_id: str,
        workspace_id: str,
        request: Request,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        datasets = await dao.list_v4_datasets(
            tenant_id=tenant_id, workspace_id=workspace_id
        )
        visible = []
        for dataset in datasets:
            try:
                await _require_dataset_roles(
                    request,
                    access_control,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    dataset_ids=[str(dataset["dataset_id"])],
                    required_role="READER",
                )
            except HTTPException:
                continue
            visible.append(dataset)
        return {"datasets": visible}

    @router.post("/catalog/documents", status_code=201)
    async def create_document(
        request: Request,
        payload: V4DocumentRequest,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        await _require_dataset_roles(
            request,
            access_control,
            tenant_id=payload.tenant_id,
            workspace_id=payload.workspace_id,
            dataset_ids=[payload.dataset_id],
            required_role="WRITER",
        )
        await _require_mutation_admission(dao)
        try:
            return await dao.create_v4_document(
                tenant_id=payload.tenant_id,
                dataset_id=payload.dataset_id,
                document_id=payload.document_id,
                title=payload.title,
                external_ref=payload.external_ref,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/catalog/documents")
    async def list_documents(
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        request: Request,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        await _require_dataset_roles(
            request,
            access_control,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            dataset_ids=[dataset_id],
            required_role="READER",
        )
        return {
            "documents": await dao.list_v4_documents(
                tenant_id=tenant_id, dataset_id=dataset_id
            )
        }

    @router.post("/catalog/revisions", status_code=201)
    async def create_revision(
        request: Request,
        payload: V4RevisionRequest,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        await _require_dataset_roles(
            request,
            access_control,
            tenant_id=payload.tenant_id,
            workspace_id=payload.workspace_id,
            dataset_ids=[payload.dataset_id],
            required_role="WRITER",
        )
        await _require_mutation_admission(dao)
        try:
            return await dao.create_v4_revision(
                tenant_id=payload.tenant_id,
                dataset_id=payload.dataset_id,
                document_id=payload.document_id,
                revision_id=payload.revision_id,
                revision_number=payload.revision_number,
                content_hash=payload.content_sha256,
                supersedes_revision_id=payload.supersedes_revision_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/catalog/revisions")
    async def list_revisions(
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        document_id: str,
        request: Request,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        await _require_dataset_roles(
            request,
            access_control,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            dataset_ids=[dataset_id],
            required_role="READER",
        )
        return {
            "revisions": await dao.list_v4_revisions(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                document_id=document_id,
            )
        }

    @router.post("/catalog/source-chunks", status_code=201)
    async def create_source_chunk(
        request: Request,
        payload: V4SourceChunkRequest,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        await _require_dataset_roles(
            request,
            access_control,
            tenant_id=payload.tenant_id,
            workspace_id=payload.workspace_id,
            dataset_ids=[payload.dataset_id],
            required_role="WRITER",
        )
        await _require_mutation_admission(dao)
        return await dao.create_v4_source_chunk(
            tenant_id=payload.tenant_id,
            dataset_id=payload.dataset_id,
            document_id=payload.document_id,
            revision_id=payload.revision_id,
            chunk_id=payload.chunk_id,
            title=payload.title,
            content_payload=payload.content,
            source_ref=payload.source_ref,
            revision_number=payload.revision_number,
            chunk_ordinal=payload.chunk_ordinal,
            finalize_revision=payload.finalize_revision,
            external_ref=payload.external_ref,
            supersedes_revision_id=payload.supersedes_revision_id,
        )

    @router.delete("/catalog/documents/{document_id}", status_code=202)
    async def purge_document(
        document_id: str,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        request: Request,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        await _require_dataset_roles(
            request,
            access_control,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            dataset_ids=[dataset_id],
            required_role="OWNER",
        )
        principal = _active_principal(request)
        if not await access_control.check_dataset_permission(
            principal.principal_id,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            permission="PURGE",
        ):
            raise HTTPException(status_code=403, detail="PURGE permission required")
        await _require_mutation_admission(dao)
        try:
            return await dao.purge_v4_document(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                document_id=document_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PurgeRetryPendingError as exc:
            raise HTTPException(
                status_code=503,
                detail="purge_cleanup_pending",
                headers={"Retry-After": "5"},
            ) from exc

    @router.post("/sessions/start", status_code=201)
    async def start_session(
        request: Request,
        payload: V4SessionStartRequest,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        principal = _active_principal(request)
        if not await access_control.check_principal_permission(
            principal.principal_id, payload.agent_id, "SESSION_CREATE"
        ):
            raise HTTPException(
                status_code=403, detail="Principal lacks SESSION_CREATE permission"
            )
        await _require_dataset_roles(
            request,
            access_control,
            tenant_id=payload.tenant_id,
            workspace_id=payload.workspace_id,
            dataset_ids=payload.dataset_ids,
            required_role="WRITER",
        )
        await _require_mutation_admission(dao)
        try:
            session = await dao.create_v4_session(
                tenant_id=payload.tenant_id,
                workspace_id=payload.workspace_id,
                dataset_ids=payload.dataset_ids,
                agent_id=payload.agent_id,
                principal_id=principal.principal_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        session_id = str(session["session_id"])
        await access_control.grant_access(payload.agent_id, session_id, "WRITE")
        await access_control.grant_principal_session_access(
            principal.principal_id, payload.agent_id, session_id, "WRITE"
        )
        return {"status": "started", **session}

    @router.get("/capability", status_code=200)
    async def get_capability(
        dao: MemoryDAO = Depends(get_dao),
    ) -> V4CapabilityResponse:
        """Return bounded capability truth without implying planned behaviour."""
        vector_available = getattr(
            getattr(dao, "_vec", None), "semantic_runtime_available", False
        )
        canonical_writes_available = dao.canonical_v4_writes_enabled is not False
        capabilities = V4CapabilityFlags(
            projection_outbox=canonical_writes_available,
            idempotent_ingestion=canonical_writes_available,
            durable_rebuild=config.v4_rebuild_enabled,
            vector_retrieval=(
                vector_available if isinstance(vector_available, bool) else False
            ),
            graph_projection=canonical_writes_available,
        )
        policy = current_validation_policy()
        if policy is not None:
            val_cap = V4ValidationCapability(
                mode=policy.mode,
                policy=policy.name,
                llm_validation_enabled=policy.llm_validation_enabled,
                validator_count=policy.validator_count,
            )
        elif config.effective_tier3_mode(model_enabled=config.model_enabled) == 0:
            val_cap = V4ValidationCapability(
                mode=0,
                policy="deterministic_only",
                llm_validation_enabled=False,
                validator_count=0,
            )
        else:
            val_cap = V4ValidationCapability(
                mode=config.effective_tier3_mode(model_enabled=config.model_enabled),
                policy="not_composed",
                llm_validation_enabled=False,
                validator_count=0,
            )

        return V4CapabilityResponse(
            features=[
                name for name, enabled in capabilities.model_dump().items() if enabled
            ],
            capabilities=capabilities,
            validation=val_cap,
        )

    async def submit_rebuild_operation(
        request: Request,
        idempotency_key: str,
        dao: MemoryDAO,
        access_control: AccessControl,
    ) -> V4OperationResponse:
        principal = await _require_control_admin(request, access_control)
        if (
            not idempotency_key
            or idempotency_key.strip() != idempotency_key
            or len(idempotency_key) > 128
            or any(ord(char) < 32 for char in idempotency_key)
        ):
            raise HTTPException(status_code=422, detail="Idempotency-Key is invalid")
        if not config.v4_rebuild_enabled:
            raise HTTPException(status_code=501, detail="Durable rebuild is disabled")
        payload_hash = hashlib.sha256(
            b'{"kind":"projection","scope":"storage_root","version":1}'
        ).hexdigest()
        try:
            operation = await dao.operations.submit(
                requested_by_principal_id=str(principal.principal_id),
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
            )
        except OperationIdempotencyConflictError as exc:
            raise HTTPException(
                status_code=409, detail="Idempotency key conflicts with request"
            ) from exc
        except OperationActiveConflictError as exc:
            raise HTTPException(
                status_code=409, detail="A projection rebuild is already active"
            ) from exc
        return _public_operation(operation)

    @router.post(
        "/operations/rebuild",
        response_model=V4OperationResponse,
        status_code=202,
    )
    async def create_rebuild_operation(
        request: Request,
        idempotency_key: str = Header(
            ...,
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
        ),
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> V4OperationResponse:
        return await submit_rebuild_operation(
            request, idempotency_key, dao, access_control
        )

    @router.get(
        "/operations/{operation_id}",
        response_model=V4OperationResponse,
        status_code=200,
    )
    async def get_rebuild_operation(
        operation_id: str,
        request: Request,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> V4OperationResponse:
        await _require_control_admin(request, access_control, hide_existence=True)
        operation = await dao.operations.get(operation_id)
        if operation is None:
            raise HTTPException(status_code=404, detail="Operation not found")
        return _public_operation(operation)

    @router.post(
        "/operations/{operation_id}/cancel",
        response_model=V4OperationResponse,
        status_code=200,
    )
    async def cancel_rebuild_operation(
        operation_id: str,
        request: Request,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> V4OperationResponse:
        await _require_control_admin(request, access_control, hide_existence=True)
        try:
            operation = await dao.operations.cancel(operation_id)
        except OperationNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Operation not found") from exc
        except OperationStateError as exc:
            raise HTTPException(
                status_code=409, detail="Operation cannot be cancelled"
            ) from exc
        return _public_operation(operation)

    @router.post(
        "/operations/{operation_id}/retry",
        response_model=V4OperationResponse,
        status_code=202,
    )
    async def retry_rebuild_operation(
        operation_id: str,
        request: Request,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> V4OperationResponse:
        await _require_control_admin(request, access_control, hide_existence=True)
        if not config.v4_rebuild_enabled:
            raise HTTPException(status_code=501, detail="Durable rebuild is disabled")
        try:
            operation = await dao.operations.retry(operation_id)
        except OperationNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Operation not found") from exc
        except OperationStateError as exc:
            raise HTTPException(
                status_code=409, detail="Operation cannot be retried"
            ) from exc
        return _public_operation(operation)

    @router.post(
        "/rebuild",
        response_model=V4OperationResponse,
        status_code=202,
        deprecated=True,
    )
    async def rebuild_index(
        request: Request,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        dataset_id: str | None = None,
        idempotency_key: str = Header(
            ...,
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
        ),
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> V4OperationResponse:
        await _require_control_admin(request, access_control)
        if tenant_id is not None or workspace_id is not None or dataset_id is not None:
            raise HTTPException(
                status_code=409,
                detail="Scoped projection rebuild is not supported",
            )
        return await submit_rebuild_operation(
            request, idempotency_key, dao, access_control
        )

    @router.post("/memory/insert", status_code=202)
    async def insert_memory(
        request: Request,
        payload: V4MemoryInsertRequest,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        session = await _authorized_v4_session(
            request,
            dao,
            access_control,
            payload.session_id,
            level="WRITE",
        )
        if payload.dataset_id not in session["dataset_ids"]:
            raise HTTPException(
                status_code=403, detail="Dataset is outside session scope"
            )
        await _require_mutation_admission(dao, require_projection_consumer=True)
        payload_hash = hashlib.sha256(
            payload.model_dump_json(exclude={"session_id", "idempotency_key"}).encode()
        ).hexdigest()
        embedding_identity = configured_embedding_identity()
        validation_policy = current_validation_policy()
        try:
            admission = await dao.admit_v4_memory(
                tenant_id=str(session["tenant_id"]),
                workspace_id=str(session["workspace_id"]),
                dataset_id=payload.dataset_id,
                agent_id=str(session["agent_id"]),
                session_id=payload.session_id,
                document_id=payload.document_id,
                revision_id=payload.revision_id,
                chunk_id=payload.chunk_id,
                title=payload.title,
                content_payload=payload.content,
                source_ref=payload.source_ref,
                evidence_span=payload.evidence_span,
                revision_number=payload.revision_number,
                chunk_ordinal=payload.chunk_ordinal,
                supersedes_revision_id=payload.supersedes_revision_id,
                metadata=payload.metadata,
                embedding_provider=embedding_identity.provider,
                embedding_model=embedding_identity.model,
                embedding_version=embedding_identity.version,
                embedding_dimension=embedding_identity.dimension,
                embedding_space_id=embedding_identity.embedding_space_id,
                embedding_model_revision=embedding_identity.model_revision,
                embedding_normalized=embedding_identity.normalized,
                policy=config.queue_admission_policy,
                validation_mode=(
                    validation_policy.mode
                    if validation_policy is not None
                    else config.effective_tier3_mode(model_enabled=config.model_enabled)
                ),
                idempotency_key=payload.idempotency_key,
                payload_hash=payload_hash if payload.idempotency_key else None,
                finalize_revision=payload.finalize_revision,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except QueueRecordTooLargeError:
            raise HTTPException(status_code=413, detail="queue_record_too_large")
        except QueueOverCapacityError:
            raise HTTPException(status_code=503, detail="queue_over_capacity")
        except QueueUnavailableError:
            raise HTTPException(status_code=503, detail="queue_unavailable")
        if admission["outcome"] == "IN_PROGRESS":
            raise HTTPException(
                status_code=409, detail="idempotency_key is in progress"
            )
        response = dict(admission["response"])
        if admission["outcome"] == "DUPLICATE":
            response["duplicate"] = True
        return response

    @router.post("/memory/search")
    async def search_memory(
        request: Request,
        payload: V4SearchRequest,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        session = await _authorized_v4_session(
            request, dao, access_control, payload.session_id, level="READ"
        )
        datasets = payload.dataset_ids or list(session["dataset_ids"])
        if not set(datasets).issubset(set(session["dataset_ids"])):
            raise HTTPException(
                status_code=403, detail="Dataset is outside session scope"
            )
        results = await dao.search_v4_memory(
            tenant_id=str(session["tenant_id"]),
            agent_id=str(session["agent_id"]),
            dataset_ids=datasets,
            query=payload.query,
            limit=payload.limit,
            jurisdiction=payload.jurisdiction,
            valid_at=payload.valid_at.isoformat() if payload.valid_at else None,
            valid_from=payload.valid_from.isoformat() if payload.valid_from else None,
            valid_to=payload.valid_to.isoformat() if payload.valid_to else None,
        )
        return {
            "session_id": payload.session_id,
            "dataset_ids": datasets,
            "results": results,
        }

    @router.get("/mutations/{mutation_id}", response_model=V4MutationStatusResponse)
    async def mutation_status(
        mutation_id: str,
        request: Request,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> V4MutationStatusResponse:
        mutation = await dao.get_mutation_summary(mutation_id)
        if mutation is None:
            raise HTTPException(status_code=404, detail="Unknown mutation")
        await _authorized_v4_session(
            request,
            dao,
            access_control,
            str(mutation["session_id"]),
            level="READ",
        )
        pipeline = (
            await dao.get_pipeline_run(str(mutation["pipeline_run_id"]))
            if mutation.get("pipeline_run_id")
            else None
        )
        return V4MutationStatusResponse(
            mutation_id=str(mutation["mutation_id"]),
            candidate_id=str(mutation["candidate_id"]),
            state=str(mutation["state"]),
            failure_class=mutation.get("failure_class"),
            rejection_reason=mutation.get("rejection_reason"),
            tier3_audit=mutation.get("tier3_audit"),
            pipeline_run=pipeline,
            artifacts=mutation["artifacts"],
            projections=mutation["projections"],
        )

    @router.post("/mutations/{mutation_id}/rollback", status_code=202)
    async def rollback_mutation(
        mutation_id: str,
        request: Request,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        mutation = await dao.get_mutation_summary(mutation_id)
        if mutation is None:
            raise HTTPException(status_code=404, detail="Unknown mutation")
        session = await _authorized_v4_session(
            request,
            dao,
            access_control,
            str(mutation["session_id"]),
            level="WRITE",
            allow_closed=True,
        )
        _require_historical_mutation_scope(mutation, session)
        principal = _active_principal(request)
        if not await access_control.check_dataset_permission(
            principal.principal_id,
            tenant_id=str(session["tenant_id"]),
            dataset_id=str(mutation["dataset_id"]),
            permission="ROLLBACK",
        ):
            raise HTTPException(status_code=403, detail="ROLLBACK permission required")
        await _require_mutation_admission(dao)
        try:
            return await dao.request_pipeline_rollback(str(mutation["pipeline_run_id"]))
        except NonHeadRollbackConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail="NON_HEAD_ROLLBACK_CONFLICT",
            ) from exc

    @router.post("/mutations/{mutation_id}/replay", status_code=202)
    async def replay_mutation(
        mutation_id: str,
        request: Request,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        mutation = await dao.get_mutation_summary(mutation_id)
        if mutation is None:
            raise HTTPException(status_code=404, detail="Unknown mutation")
        session = await _authorized_v4_session(
            request,
            dao,
            access_control,
            str(mutation["session_id"]),
            level="WRITE",
            allow_closed=True,
        )
        _require_historical_mutation_scope(mutation, session)
        principal = _active_principal(request)
        if not await access_control.check_dataset_permission(
            principal.principal_id,
            tenant_id=str(session["tenant_id"]),
            dataset_id=str(mutation["dataset_id"]),
            permission="ROLLBACK",
        ):
            raise HTTPException(status_code=403, detail="ROLLBACK permission required")
        await _require_mutation_admission(dao)
        try:
            return await dao.replay_pipeline_run(
                str(mutation["pipeline_run_id"]),
                target_mutation_id=mutation_id,
            )
        except NonReplayableMutationConflictError as exc:
            raise HTTPException(status_code=409, detail="NON_REPLAYABLE") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/sessions/{session_id}/context")
    async def get_context(
        session_id: str,
        request: Request,
        query: str = "",
        token_budget: int = 2048,
        valid_at: datetime | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        session = await _authorized_v4_session(
            request, dao, access_control, session_id, level="READ"
        )
        if valid_from and valid_to and valid_from > valid_to:
            raise HTTPException(
                status_code=422, detail="valid_from must not be after valid_to"
            )
        agent_id = str(session["agent_id"])
        builder = ContextBuilder(dao)
        ctx = await builder.build_context(
            tenant_id=str(session["tenant_id"]),
            agent_id=agent_id,
            dataset_ids=list(session.get("dataset_ids", [])),
            query=query,
            session_id=session_id,
            token_budget=token_budget,
            valid_at=valid_at.isoformat() if valid_at else None,
            valid_from=valid_from.isoformat() if valid_from else None,
            valid_to=valid_to.isoformat() if valid_to else None,
        )
        mutations = await dao.list_session_mutation_summaries(
            agent_id, session_id, limit=20
        )
        return {
            "tenant_id": session["tenant_id"],
            "workspace_id": session["workspace_id"],
            "dataset_ids": session["dataset_ids"],
            "agent_id": agent_id,
            "session_id": session_id,
            "context": ctx["formatted_context"],
            "canonical_memories": ctx["canonical_memories"],
            "estimated_token_count": ctx["estimated_token_count"],
            "mutations": mutations,
        }

    @router.post("/sessions/{session_id}/end")
    async def end_session(
        session_id: str,
        request: Request,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        session = await _authorized_v4_session(
            request, dao, access_control, session_id, level="WRITE"
        )
        await _require_mutation_admission(dao)
        finalization = await dao.request_session_finalization(
            str(session["agent_id"]), session_id
        )
        await dao.end_v4_session(session_id)
        return {
            "status": "ended" if finalization["state"] == "COMPLETED" else "pending",
            "session_id": session_id,
            "finalization_id": finalization["finalization_id"],
        }

    return router
