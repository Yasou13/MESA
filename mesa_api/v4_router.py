"""Versioned V4 full-cognitive API contract.

V3 remains the lexical-core compatibility surface.  V4 admission creates a
canonical mutation before background processing and every later operation is
authorized against a verified principal-to-agent-to-session binding.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from mesa_memory.config import config
from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import (
    MemoryDAO,
    QueueOverCapacityError,
    QueueRecordTooLargeError,
    QueueUnavailableError,
)

logger = logging.getLogger("MESA_V4_API")


class V4MutationStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    mutation_id: str
    candidate_id: str
    state: str
    failure_class: str | None = None
    pipeline_run: dict | None = None
    artifacts: list[dict] = Field(default_factory=list)
    projections: list[dict] = Field(default_factory=list)


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
    supersedes_revision_id: str | None = Field(default=None, max_length=256)
    metadata: dict = Field(default_factory=dict)


class V4SearchRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    session_id: str = Field(min_length=1, max_length=128)
    dataset_ids: list[str] | None = Field(default=None, max_length=64)
    query: str = Field(min_length=1, max_length=4096)
    limit: int = Field(default=10, ge=1, le=50)
    jurisdiction: str | None = Field(default=None, max_length=256)
    valid_at: datetime | None = None


def _active_principal(request: Request):  # type: ignore[no-untyped-def]
    principal = getattr(request.state, "principal", None)
    if principal is None or getattr(principal, "status", None) != "active":
        raise HTTPException(
            status_code=401, detail="Active authenticated principal required"
        )
    return principal


async def _require_session_access(
    request: Request,
    access_control: AccessControl,
    *,
    agent_id: str,
    session_id: str,
    level: str,
) -> None:
    principal = _active_principal(request)
    if not await access_control.check_principal_session_access(
        principal.principal_id, agent_id, session_id, level
    ):
        raise HTTPException(status_code=403, detail="Principal/session access denied")
    if not await access_control.check_access(agent_id, session_id, level):
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
) -> dict:
    session = await dao.get_v4_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session")
    if session["status"] != "ACTIVE" and level == "WRITE":
        raise HTTPException(status_code=409, detail="Session is not active")
    await _require_session_access(
        request,
        access_control,
        agent_id=str(session["agent_id"]),
        session_id=session_id,
        level=level,
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


def create_v4_router(
    get_dao: Callable[[], MemoryDAO],
    *,
    get_access_control: Callable[[], AccessControl],
) -> APIRouter:
    router = APIRouter(prefix="/v4", tags=["v4-full-cognitive"])

    @router.post(  # type: ignore[untyped-decorator]
        "/catalog/workspaces", status_code=201
    )
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
        try:
            return await dao.create_v4_workspace(
                tenant_id=payload.tenant_id,
                workspace_id=payload.workspace_id,
                tenant_name=payload.tenant_name,
                workspace_name=payload.workspace_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/catalog/workspaces")  # type: ignore[untyped-decorator]
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

    @router.post("/catalog/datasets", status_code=201)  # type: ignore[untyped-decorator]
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
        return cast(dict[str, Any], payload.model_dump())

    @router.get("/catalog/datasets")  # type: ignore[untyped-decorator]
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

    @router.post(  # type: ignore[untyped-decorator]
        "/catalog/documents", status_code=201
    )
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

    @router.get("/catalog/documents")  # type: ignore[untyped-decorator]
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

    @router.post(  # type: ignore[untyped-decorator]
        "/catalog/revisions", status_code=201
    )
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
        try:
            return await dao.create_v4_revision(
                tenant_id=payload.tenant_id,
                document_id=payload.document_id,
                revision_id=payload.revision_id,
                revision_number=payload.revision_number,
                content_hash=payload.content_sha256,
                supersedes_revision_id=payload.supersedes_revision_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/catalog/revisions")  # type: ignore[untyped-decorator]
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
                tenant_id=tenant_id, document_id=document_id
            )
        }

    @router.post(  # type: ignore[untyped-decorator]
        "/catalog/source-chunks", status_code=201
    )
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
            external_ref=payload.external_ref,
            supersedes_revision_id=payload.supersedes_revision_id,
        )

    @router.delete(  # type: ignore[untyped-decorator]
        "/catalog/documents/{document_id}", status_code=202
    )
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
        try:
            return await dao.purge_v4_document(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                document_id=document_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @router.post("/sessions/start", status_code=201)  # type: ignore[untyped-decorator]
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

    @router.post("/memory/insert", status_code=202)  # type: ignore[untyped-decorator]
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
        await dao.create_v4_source_chunk(
            tenant_id=str(session["tenant_id"]),
            dataset_id=payload.dataset_id,
            document_id=payload.document_id,
            revision_id=payload.revision_id,
            chunk_id=payload.chunk_id,
            title=payload.title,
            content_payload=payload.content,
            source_ref=payload.source_ref,
            revision_number=payload.revision_number,
            chunk_ordinal=payload.chunk_ordinal,
            supersedes_revision_id=payload.supersedes_revision_id,
        )
        raw_payload = {
            "tenant_id": session["tenant_id"],
            "workspace_id": session["workspace_id"],
            "dataset_id": payload.dataset_id,
            "document_id": payload.document_id,
            "revision_id": payload.revision_id,
            "chunk_id": payload.chunk_id,
            "source_ref": payload.source_ref,
            "agent_id": session["agent_id"],
            "session_id": payload.session_id,
            "content": payload.content,
            "metadata": payload.metadata,
        }
        try:
            admission = await dao.admit_raw_log(
                str(session["agent_id"]),
                raw_payload,
                policy=config.queue_admission_policy,
            )
        except QueueRecordTooLargeError:
            raise HTTPException(status_code=413, detail="queue_record_too_large")
        except QueueOverCapacityError:
            raise HTTPException(status_code=503, detail="queue_over_capacity")
        except QueueUnavailableError:
            raise HTTPException(status_code=503, detail="queue_unavailable")

        candidate = MemoryCandidate.from_raw_log(
            raw_log_id=int(admission["log_id"]),
            tenant_id=str(session["tenant_id"]),
            workspace_id=str(session["workspace_id"]),
            dataset_id=payload.dataset_id,
            document_id=payload.document_id,
            revision_id=payload.revision_id,
            chunk_id=payload.chunk_id,
            source_ref=payload.source_ref,
            evidence_span=payload.evidence_span,
            agent_id=str(session["agent_id"]),
            session_id=payload.session_id,
            content_payload=payload.content,
            metadata=payload.metadata,
        )
        await dao.record_mutation(
            candidate.as_consolidation_record(), raw_log_id=int(admission["log_id"])
        )
        return {
            "status": "accepted",
            "mutation_id": candidate.mutation_id,
            "candidate_id": candidate.candidate_id,
            "pipeline_run_id": candidate.pipeline_run_id,
            "raw_log_id": admission["log_id"],
        }

    @router.post("/memory/search")  # type: ignore[untyped-decorator]
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
        )
        return {
            "session_id": payload.session_id,
            "dataset_ids": datasets,
            "results": results,
        }

    @router.get(  # type: ignore[untyped-decorator]
        "/mutations/{mutation_id}", response_model=V4MutationStatusResponse
    )
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
            pipeline_run=pipeline,
            artifacts=mutation["artifacts"],
            projections=mutation["projections"],
        )

    @router.post(  # type: ignore[untyped-decorator]
        "/mutations/{mutation_id}/rollback", status_code=202
    )
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
        )
        principal = _active_principal(request)
        if not await access_control.check_dataset_permission(
            principal.principal_id,
            tenant_id=str(session["tenant_id"]),
            dataset_id=str(mutation["dataset_id"]),
            permission="ROLLBACK",
        ):
            raise HTTPException(status_code=403, detail="ROLLBACK permission required")
        return await dao.request_pipeline_rollback(str(mutation["pipeline_run_id"]))

    @router.post(  # type: ignore[untyped-decorator]
        "/mutations/{mutation_id}/replay", status_code=202
    )
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
        )
        principal = _active_principal(request)
        if not await access_control.check_dataset_permission(
            principal.principal_id,
            tenant_id=str(session["tenant_id"]),
            dataset_id=str(mutation["dataset_id"]),
            permission="ROLLBACK",
        ):
            raise HTTPException(status_code=403, detail="ROLLBACK permission required")
        return await dao.replay_pipeline_run(str(mutation["pipeline_run_id"]))

    @router.get("/sessions/{session_id}/context")  # type: ignore[untyped-decorator]
    async def get_context(
        session_id: str,
        request: Request,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        session = await _authorized_v4_session(
            request, dao, access_control, session_id, level="READ"
        )
        agent_id = str(session["agent_id"])
        raw_logs = await dao.get_recent_logs(agent_id, session_id, limit=20)
        mutations = await dao.list_session_mutation_summaries(
            agent_id, session_id, limit=20
        )
        return {
            "tenant_id": session["tenant_id"],
            "workspace_id": session["workspace_id"],
            "dataset_ids": session["dataset_ids"],
            "agent_id": agent_id,
            "session_id": session_id,
            "context": "\n".join(
                str(item.get("content", "")) for item in raw_logs if item.get("content")
            ),
            "mutations": mutations,
        }

    @router.post("/sessions/{session_id}/end")  # type: ignore[untyped-decorator]
    async def end_session(
        session_id: str,
        request: Request,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> dict:
        session = await _authorized_v4_session(
            request, dao, access_control, session_id, level="WRITE"
        )
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
