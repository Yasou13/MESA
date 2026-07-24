# mypy: disable-error-code="no-untyped-def"
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel


def create_control_router(
    get_client_repo: Callable,
    get_conn_repo: Callable,
    get_settings_repo: Callable,
    get_policy_repo: Callable,
    get_activity_repo: Callable,
    get_approval_repo: Callable,
    prefix: str = "/control/mcp",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["mcp-control"])

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
        decided_by: str
        reason: str | None = None

    @router.post("/approvals/{approval_id}/decide")
    async def decide_approval_endpoint(
        approval_id: str, req: DecideApprovalReq, repo=Depends(get_approval_repo)
    ):
        if req.status not in ("APPROVED", "REJECTED"):
            raise HTTPException(
                status_code=400, detail="Status must be APPROVED or REJECTED"
            )
        await repo.decide_approval(approval_id, req.status, req.decided_by, req.reason)
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
