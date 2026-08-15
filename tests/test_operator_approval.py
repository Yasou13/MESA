from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet

from mesa_mcp.codex_cli import _execute_operation_decision, _parser
from mesa_mcp.gateway.approval import OperationApprovalService
from mesa_mcp.gateway.middleware import ControlPlaneMiddleware
from mesa_mcp.gateway.operations import GatewayOperationService
from mesa_memory.security.rbac import AccessControl
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


class _V4Boundary:
    def __init__(self) -> None:
        self.remember_calls = 0

    async def v4_remember(self, **_kwargs: Any) -> dict[str, Any]:
        self.remember_calls += 1
        return {"status": "accepted", "mutation_id": "mut-operator"}

    async def v4_mutation_status(self, _mutation_id: str) -> dict[str, Any]:
        return {"mutation_id": "mut-operator", "state": "COMMITTED"}


@pytest.fixture()
async def approval_lifecycle(tmp_path):
    engine = AsyncEngine(str(tmp_path / "mesa.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    middleware = ControlPlaneMiddleware(engine=engine)
    await middleware.initialize()
    await middleware.client_repo.create_client(
        "operator-client", "Operator client", "codex", "memory-agent"
    )
    binding_id = await middleware.client_repo.add_project_binding(
        "operator-client", "sha256:operator", "tenant-a", "workspace-a", "dataset-a"
    )
    access = AccessControl(str(tmp_path / "rbac.sqlite"))
    await access.initialize()
    await access.grant_control_role("operator-admin")
    boundary = _V4Boundary()
    gateway = GatewayOperationService(
        engine=engine,
        middleware=middleware,
        v4_service=boundary,  # type: ignore[arg-type]
        encryption_key=Fernet.generate_key().decode(),
    )
    approval = OperationApprovalService(engine=engine, access_control=access)
    operation = await gateway._create_operation(
        "operator-client",
        {
            "binding_id": binding_id,
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_id": "dataset-a",
        },
        "credential:test",
        "mesa_remember",
        "operator-idempotency",
        {
            "content": "Operators approve durable memories.",
            "idempotency_key": "operator-idempotency",
        },
    )
    approval_id = "apr-operator"
    await middleware.approval_repo.create_approval_request(
        approval_id=approval_id,
        call_id=operation["operation_id"],
        client_id="operator-client",
        operation="WRITE",
        request_summary="operator approval test",
        payload_hash=operation["payload_hash"],
        payload_encrypted=operation["payload_encrypted"],
    )
    await gateway._set_operation(
        operation["operation_id"], "PENDING_APPROVAL", approval_id=approval_id
    )
    try:
        yield approval, gateway, middleware, boundary, operation["operation_id"]
    finally:
        await access.close()
        await middleware.close()


@pytest.mark.asyncio
async def test_operator_can_approve_pending_operation(approval_lifecycle) -> None:
    approval, gateway, middleware, _boundary, operation_id = approval_lifecycle

    result = await approval.decide(
        operation_id=operation_id,
        decision="APPROVED",
        decided_by="operator-admin",
        reason="reviewed source",
    )

    assert result == {
        "operation_id": operation_id,
        "status": "APPROVED",
        "decision": "APPROVED",
    }
    operation = await gateway._get_operation(operation_id)
    assert operation is not None and operation["status"] == "APPROVED"
    request = await middleware.approval_repo.get_approval_request("apr-operator")
    assert request is not None
    assert request["status"] == "APPROVED"
    assert request["decided_by"] == "operator-admin"
    assert request["decision_reason"] == "reviewed source"


@pytest.mark.asyncio
async def test_operator_can_reject_pending_operation(approval_lifecycle) -> None:
    approval, gateway, middleware, boundary, operation_id = approval_lifecycle

    result = await approval.decide(
        operation_id=operation_id,
        decision="REJECTED",
        decided_by="operator-admin",
        reason="insufficient provenance",
    )

    assert result["status"] == "REJECTED"
    operation = await gateway._get_operation(operation_id)
    assert operation is not None and operation["status"] == "REJECTED"
    request = await middleware.approval_repo.get_approval_request("apr-operator")
    assert request is not None and request["status"] == "REJECTED"
    assert await gateway.process_approved_operations() == 0
    assert boundary.remember_calls == 0


@pytest.mark.asyncio
async def test_operator_approval_fails_closed_for_unauthorized_actor(
    approval_lifecycle,
) -> None:
    approval, _gateway, _middleware, _boundary, operation_id = approval_lifecycle

    with pytest.raises(PermissionError, match="control administrator"):
        await approval.decide(
            operation_id=operation_id,
            decision="APPROVED",
            decided_by="unauthorized-operator",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_state", ("COMMITTED", "FAILED", "REJECTED", "CANCELLED")
)
async def test_operator_approval_fails_closed_for_non_pending_state(
    approval_lifecycle, terminal_state: str
) -> None:
    approval, gateway, _middleware, _boundary, operation_id = approval_lifecycle
    await gateway._set_operation(operation_id, terminal_state)

    with pytest.raises(ValueError, match="PENDING_APPROVAL"):
        await approval.decide(
            operation_id=operation_id,
            decision="APPROVED",
            decided_by="operator-admin",
        )


@pytest.mark.asyncio
async def test_repeated_approval_is_rejected_and_dispatches_once(
    approval_lifecycle,
) -> None:
    approval, gateway, _middleware, boundary, operation_id = approval_lifecycle
    await approval.decide(
        operation_id=operation_id,
        decision="APPROVED",
        decided_by="operator-admin",
    )

    with pytest.raises(ValueError, match="PENDING_APPROVAL"):
        await approval.decide(
            operation_id=operation_id,
            decision="APPROVED",
            decided_by="operator-admin",
        )

    assert await gateway.process_approved_operations() == 1
    assert await gateway.process_approved_operations() == 0
    assert boundary.remember_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "decision"), (("approve", "APPROVED"), ("reject", "REJECTED"))
)
async def test_mesa_operations_cli_decides_by_operation_id(
    approval_lifecycle, capsys, command: str, decision: str
) -> None:
    approval, gateway, _middleware, _boundary, operation_id = approval_lifecycle
    args = _parser().parse_args(
        [
            "operations",
            command,
            operation_id,
            "--control-db",
            gateway._engine.db_path,
            "--policy-db",
            approval._access_control.policy_path,
            "--principal",
            "operator-admin",
            "--reason",
            "reviewed in CLI",
        ]
    )

    await _execute_operation_decision(args)

    assert args.group == "operations"
    assert args.command == command
    assert __import__("json").loads(capsys.readouterr().out) == {
        "operation_id": operation_id,
        "status": decision,
        "decision": decision,
    }
