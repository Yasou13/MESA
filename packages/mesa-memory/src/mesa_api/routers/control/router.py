# mypy: disable-error-code="no-untyped-def,untyped-decorator"
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel


def create_control_router(
    get_client_repo: Callable,
    get_conn_repo: Callable,
    get_settings_repo: Callable,
    get_policy_repo: Callable,
    get_activity_repo: Callable,
    get_approval_repo: Callable,
    get_credential_repo: Callable | None = None,
    get_codex_profile_repo: Callable | None = None,
    get_access_control: Callable | None = None,
    prefix: str = "/control/mcp",
) -> APIRouter:
    async def require_control_admin(request: Request) -> None:
        principal = getattr(request.state, "principal", None)
        if principal is None or getattr(principal, "status", None) != "active":
            raise HTTPException(
                status_code=401, detail="Active authenticated principal required"
            )
        if get_access_control is None:
            raise HTTPException(status_code=503, detail="Control authorization unavailable")
        if not await get_access_control().check_control_role(
            str(principal.principal_id), "ADMIN"
        ):
            raise HTTPException(status_code=403, detail="Control administrator role required")

    router = APIRouter(
        prefix=prefix,
        tags=["mcp-control"],
        dependencies=[Depends(require_control_admin)],
    )

    class ClientCreateReq(BaseModel):
        client_id: str
        display_name: str
        client_type: str
        principal_id: str
        metadata: dict[str, Any] = {}

    @router.post("/clients", status_code=201)
    async def create_client(req: ClientCreateReq, repo=Depends(get_client_repo)):
        await repo.create_client(
            client_id=req.client_id,
            display_name=req.display_name,
            client_type=req.client_type,
            principal_id=req.principal_id,
            metadata=req.metadata,
        )
        return {"status": "created", "client_id": req.client_id}

    @router.get("/clients")
    async def list_clients(repo=Depends(get_client_repo)):
        clients = await repo.list_clients()
        return {"clients": clients}

    @router.get("/clients/{client_id}")
    async def get_client(client_id: str, repo=Depends(get_client_repo)):
        client = await repo.get_client(client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        return client

    @router.get("/connections")
    async def list_connections(
        client_id: str | None = None, repo=Depends(get_conn_repo)
    ):
        conns = await repo.list_active_connections(client_id=client_id)
        return {"connections": conns}

    @router.get("/settings")
    async def get_settings(repo=Depends(get_settings_repo)):
        settings = await repo.get_all_settings()
        return {"settings": settings}

    @router.post("/settings")
    async def update_setting(key: str, value: Any, repo=Depends(get_settings_repo)):
        await repo.set_setting(key, value)
        return {"status": "updated", "key": key}

    class PolicyCreateReq(BaseModel):
        rule_id: str
        scope_type: str
        operation: str
        effect: str
        created_by: str
        scope_id: str | None = None
        priority: int = 100
        conditions: dict[str, Any] = {}

    @router.post("/policies", status_code=201)
    async def create_policy(req: PolicyCreateReq, repo=Depends(get_policy_repo)):
        await repo.create_rule(
            rule_id=req.rule_id,
            scope_type=req.scope_type,
            operation=req.operation,
            effect=req.effect,
            created_by=req.created_by,
            scope_id=req.scope_id,
            priority=req.priority,
            conditions=req.conditions,
        )
        return {"status": "created", "rule_id": req.rule_id}

    @router.get("/policies")
    async def list_policies(
        operation: str | None = None, repo=Depends(get_policy_repo)
    ):
        rules = await repo.list_rules(operation=operation)
        return {"rules": rules}

    @router.put("/clients/{client_id}/enabled")
    async def toggle_client_enabled(
        client_id: str, enabled: bool, repo=Depends(get_client_repo)
    ):
        await repo.toggle_client_enabled(client_id, enabled)
        return {"status": "updated", "client_id": client_id, "enabled": enabled}

    @router.get("/clients/{client_id}/bindings")
    async def list_bindings(client_id: str, repo=Depends(get_client_repo)):
        bindings = await repo.list_bindings(client_id)
        return {"bindings": bindings}

    @router.get("/managed-clients")
    @router.get("/codex", include_in_schema=False)
    async def list_managed_clients(
        client_repo=Depends(get_client_repo),
        conn_repo=Depends(get_conn_repo),
        approval_repo=Depends(get_approval_repo),
    ):
        """Dashboard-safe Codex state; credentials are summaries only."""
        if get_credential_repo is None or get_codex_profile_repo is None:
            raise HTTPException(
                status_code=503, detail="Codex control plane unavailable"
            )
        credential_repo = get_credential_repo()
        profile_repo = get_codex_profile_repo()
        active = await conn_repo.list_active_connections()
        pending = await approval_repo.list_pending_approvals()
        result = []
        for client in await client_repo.list_clients():
            if client.get("client_type") not in {"codex", "antigravity"}:
                continue
            bindings = await client_repo.list_bindings(client["client_id"])
            entries = []
            for binding in bindings:
                entries.append(
                    {
                        "binding": binding,
                        "profile": await profile_repo.get(binding["binding_id"]),
                        "credentials": await credential_repo.list_for_binding(
                            binding["binding_id"]
                        ),
                        "active_connections": sum(
                            1
                            for connection in active
                            if connection["client_id"] == client["client_id"]
                            and connection.get("project_id")
                            == binding["external_project_id"]
                        ),
                        "pending_approvals": sum(
                            1
                            for approval in pending
                            if approval["client_id"] == client["client_id"]
                        ),
                    }
                )
            result.append({"client": client, "bindings": entries})
        return {"clients": result}

    @router.post("/credentials/{credential_id}/revoke")
    @router.post("/codex/credentials/{credential_id}/revoke", include_in_schema=False)
    async def revoke_managed_credential(credential_id: str):
        if get_credential_repo is None:
            raise HTTPException(
                status_code=503, detail="Codex control plane unavailable"
            )
        credential = await get_credential_repo().get_summary(credential_id)
        if credential is None:
            raise HTTPException(status_code=404, detail="Credential not found")
        if not await get_credential_repo().revoke(credential_id):
            raise HTTPException(status_code=409, detail="Credential is not active")
        return {"status": "revoked", "credential_id": credential_id}

    @router.get("/activity")
    async def list_activity(
        limit: int = 50,
        offset: int = 0,
        client_id: str | None = None,
        status: str | None = None,
        repo=Depends(get_activity_repo),
    ):
        calls = await repo.list_recent_calls(
            limit=limit, offset=offset, client_id=client_id, status=status
        )
        return {"activity": calls}

    @router.get("/activity/{call_id}")
    async def get_activity_call(call_id: str, repo=Depends(get_activity_repo)):
        call = await repo.get_call(call_id)
        if not call:
            raise HTTPException(status_code=404, detail="Call not found")
        return call

    @router.get("/approvals")
    async def list_approvals(
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        repo=Depends(get_approval_repo),
    ):
        approvals = await repo.list_approvals(status=status, limit=limit, offset=offset)
        return {"approvals": approvals}

    @router.get("/approvals/pending")
    async def list_pending_approvals_endpoint(
        client_id: str | None = None, repo=Depends(get_approval_repo)
    ):
        approvals = await repo.list_pending_approvals(client_id=client_id)
        return {"approvals": approvals}

    class DecideApprovalReq(BaseModel):
        status: str
        reason: str | None = None

    @router.post("/approvals/{approval_id}/decide")
    async def decide_approval_endpoint(
        approval_id: str,
        req: DecideApprovalReq,
        request: Request,
        repo=Depends(get_approval_repo),
    ):
        if req.status not in ("APPROVED", "REJECTED"):
            raise HTTPException(
                status_code=400, detail="Status must be APPROVED or REJECTED"
            )
        principal = getattr(request.state, "principal", None)
        if principal is None or getattr(principal, "status", None) != "active":
            raise HTTPException(
                status_code=401, detail="Active authenticated principal required"
            )
        decided = await repo.decide_approval(
            approval_id, req.status, str(principal.principal_id), req.reason
        )
        if not decided:
            if await repo.get_approval_request(approval_id) is None:
                raise HTTPException(status_code=404, detail="Approval not found")
            raise HTTPException(status_code=409, detail="Approval is no longer pending")
        return {"status": "decided", "approval_id": approval_id, "decision": req.status}

    @router.get("/overview")
    async def get_overview(
        client_repo=Depends(get_client_repo),
        conn_repo=Depends(get_conn_repo),
        activity_repo=Depends(get_activity_repo),
        approval_repo=Depends(get_approval_repo),
    ):
        clients = await client_repo.list_clients()
        conns = await conn_repo.count_by_status()
        pending = await approval_repo.count_pending()
        calls = await activity_repo.count_calls_by_status()

        return {
            "total_clients": len(clients),
            "active_clients": len([c for c in clients if c.get("enabled")]),
            "connections_by_status": conns,
            "pending_approvals": pending,
            "calls_by_status": calls,
        }

    return router
