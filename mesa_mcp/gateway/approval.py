"""Canonical operator decisions for durable MCP operations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite

from mesa_memory.security.rbac import AccessControl
from mesa_storage.sqlite_engine import AsyncEngine


class OperationApprovalService:
    """Authorize and atomically decide one pending gateway operation."""

    def __init__(self, *, engine: AsyncEngine, access_control: AccessControl) -> None:
        self._engine = engine
        self._access_control = access_control

    async def decide(
        self,
        *,
        operation_id: str,
        decision: str,
        decided_by: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        normalized_decision = decision.strip().upper()
        if normalized_decision not in {"APPROVED", "REJECTED"}:
            raise ValueError("decision must be APPROVED or REJECTED")
        if not operation_id.strip():
            raise ValueError("operation_id is required")
        if not decided_by.strip():
            raise ValueError("operator principal is required")
        if not await self._access_control.check_control_role(decided_by, "ADMIN"):
            raise PermissionError("control administrator role required")

        now = datetime.now(timezone.utc).isoformat()
        operation_status = (
            "APPROVED" if normalized_decision == "APPROVED" else "REJECTED"
        )
        async with self._engine.transaction() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    operation.operation_id,
                    operation.status AS operation_status,
                    operation.client_id,
                    operation.binding_id,
                    operation.approval_id,
                    approval.call_id,
                    approval.client_id AS approval_client_id,
                    approval.status AS approval_status,
                    binding.enabled AS binding_enabled,
                    client.enabled AS client_enabled
                FROM mcp_operations AS operation
                LEFT JOIN mcp_approval_requests AS approval
                    ON approval.approval_id = operation.approval_id
                LEFT JOIN mcp_project_bindings AS binding
                    ON binding.binding_id = operation.binding_id
                    AND binding.client_id = operation.client_id
                LEFT JOIN mcp_clients AS client
                    ON client.client_id = operation.client_id
                WHERE operation.operation_id = ?
                """,
                (operation_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise ValueError("operation not found")
            operation = dict(row)
            if not operation.get("binding_enabled") or not operation.get(
                "client_enabled"
            ):
                raise PermissionError("operation scope is no longer active")
            if operation["operation_status"] != "PENDING_APPROVAL":
                raise ValueError("operation is not in PENDING_APPROVAL")
            if (
                not operation.get("approval_id")
                or operation.get("call_id") != operation_id
                or operation.get("approval_client_id") != operation.get("client_id")
                or operation.get("approval_status") != "PENDING"
            ):
                raise ValueError("operation does not have a pending approval request")

            approval_cursor = await db.execute(
                """
                UPDATE mcp_approval_requests
                SET status = ?, decided_at = ?, decided_by = ?, decision_reason = ?
                WHERE approval_id = ? AND status = 'PENDING'
                """,
                (
                    normalized_decision,
                    now,
                    decided_by,
                    reason,
                    operation["approval_id"],
                ),
            )
            operation_cursor = await db.execute(
                """
                UPDATE mcp_operations
                SET status = ?,
                    error_code = CASE WHEN ? = 'REJECTED' THEN 'OPERATOR_REJECTED' ELSE NULL END,
                    error_message = CASE WHEN ? = 'REJECTED' THEN ? ELSE NULL END,
                    updated_at = ?,
                    completed_at = CASE WHEN ? = 'REJECTED' THEN ? ELSE NULL END
                WHERE operation_id = ? AND status = 'PENDING_APPROVAL'
                """,
                (
                    operation_status,
                    operation_status,
                    operation_status,
                    reason or "Operator rejected the operation",
                    now,
                    operation_status,
                    now,
                    operation_id,
                ),
            )
            if approval_cursor.rowcount != 1 or operation_cursor.rowcount != 1:
                await db.rollback()
                raise ValueError("operation is not in PENDING_APPROVAL")
            await db.commit()

        return {
            "operation_id": operation_id,
            "status": operation_status,
            "decision": normalized_decision,
        }
