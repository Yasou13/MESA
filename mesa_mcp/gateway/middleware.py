import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any, Awaitable, Callable

from mesa_mcp.gateway.policy.engine import PolicyEngine
from mesa_storage.control.activity_repo import ActivityRecorder
from mesa_storage.control.approval_repo import ApprovalRepository
from mesa_storage.control.client_repo import ClientRepository
from mesa_storage.control.codex_profile_repo import BindingContextProfileRepository
from mesa_storage.control.connection_repo import ConnectionRepository
from mesa_storage.control.credential_repo import CredentialRepository
from mesa_storage.control.policy_repo import PolicyRepository
from mesa_storage.control.settings_repo import SettingsRepository
from mesa_storage.sqlite_engine import AsyncEngine

logger = logging.getLogger(__name__)


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def audit_payload_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep operator-visible audit data free of caller-provided values."""
    return {
        "argument_keys": sorted(str(key) for key in payload),
        "payload_sha256": canonical_payload_hash(payload),
    }


class ControlPlaneMiddleware:
    def __init__(
        self, db_path: str = "./storage/mesa.db", engine: AsyncEngine | None = None
    ):
        self.engine = engine or AsyncEngine(db_path)
        self.client_repo = ClientRepository(self.engine)
        self.conn_repo = ConnectionRepository(self.engine)
        self.credential_repo = CredentialRepository(self.engine)
        self.binding_profile_repo = BindingContextProfileRepository(self.engine)
        self.codex_profile_repo = self.binding_profile_repo
        self.policy_repo = PolicyRepository(self.engine)
        self.settings_repo = SettingsRepository(self.engine)
        self.activity_repo = ActivityRecorder(self.engine)
        self.approval_repo = ApprovalRepository(self.engine)
        self.policy_engine = PolicyEngine(self.policy_repo, self.settings_repo)
        self._initialized = True if engine else False
        self._active_connections: dict[str, str] = {}

    async def initialize(self):
        if not self._initialized:
            await self.engine.initialize()
            self._initialized = True

    async def close(self):
        if self._initialized:
            await self.engine.close()
            self._initialized = False

    def _map_tool_to_operation(self, tool_name: str) -> str:
        mapping = {
            "mesa_health": "HEALTH",
            "mesa_search_memory": "SEARCH",
            "mesa_get_memory": "READ",
            "mesa_get_context": "CONTEXT",
            "mesa_store_memory": "WRITE",
            "mesa_remember": "WRITE",
            "mesa_recall": "SEARCH",
            "mesa_improve": "UPDATE",
            "mesa_forget": "DELETE",
        }
        return mapping.get(tool_name, "UNKNOWN")

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        await self.initialize()

        call_id = f"call-{uuid.uuid4().hex}"
        trace_id = f"trace-{uuid.uuid4().hex}"

        client_id = os.environ.get("MESA_CLIENT_ID", "default-mcp-client")
        principal_id = os.environ.get("MESA_PRINCIPAL_ID", "local-user")
        project_id = arguments.get("project_id")

        operation = self._map_tool_to_operation(tool_name)

        # Ensure client exists
        client = await self.client_repo.get_client(client_id)
        if not client:
            await self.client_repo.create_client(
                client_id, "Auto-registered Client", "stdio", principal_id
            )

        # Register or reuse connection
        conn_id = self._active_connections.get(client_id)
        if not conn_id:
            conn_id = f"conn-{uuid.uuid4().hex}"
            await self.conn_repo.register_connection(
                conn_id, client_id, "stdio", status="CONNECTED"
            )
            self._active_connections[client_id] = conn_id

        # Evaluate Policy
        effect = await self.policy_engine.evaluate(client_id, project_id, operation)

        # Record Activity Start
        await self.activity_repo.record_call_start(
            call_id=call_id,
            trace_id=trace_id,
            client_id=client_id,
            tool_name=tool_name,
            operation_type=operation,
            decision=effect,
            connection_id=conn_id,
            principal_id=principal_id,
            metadata=audit_payload_metadata(arguments),
        )

        start_time = time.time()

        try:
            if effect == "DENY":
                result = {
                    "error": "DENIED",
                    "message": "Policy engine denied this operation.",
                }
                await self.activity_repo.record_call_completion(
                    call_id, "DENIED", error_message="Policy denied"
                )
                return result

            elif effect == "REQUIRE_APPROVAL":
                payload_hash = canonical_payload_hash(arguments)
                approval_id = f"appr-{uuid.uuid4().hex}"

                await self.approval_repo.create_approval_request(
                    approval_id=approval_id,
                    call_id=call_id,
                    client_id=client_id,
                    operation=operation,
                    request_summary=f"Tool {tool_name} requested execution",
                    payload_hash=payload_hash,
                )
                result = {
                    "status": "PENDING_APPROVAL",
                    "approval_id": approval_id,
                    "message": "This operation requires manual approval from the dashboard.",
                }
                await self.activity_repo.record_call_completion(
                    call_id, "PENDING_APPROVAL"
                )
                return result

            else:  # ALLOW
                result = await handler(arguments)
                duration_ms = int((time.time() - start_time) * 1000)

                status = "SUCCESS"
                error_message = None
                if isinstance(result, dict) and "error" in result:
                    status = "ERROR"
                    error_message = result.get("message", "Unknown error")

                await self.activity_repo.record_call_completion(
                    call_id, status, duration_ms, error_message
                )
                return result

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.exception("Tool execution failed")
            await self.activity_repo.record_call_completion(
                call_id, "ERROR", duration_ms, str(e)
            )
            raise
