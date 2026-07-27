"""Durable operation module behind the Antigravity gateway interface."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet

from mesa_storage.sqlite_engine import AsyncEngine

from ..errors import MCPError
from ..v4_service import MesaHttpV4Service
from .auth import GatewayPrincipal
from .middleware import ControlPlaneMiddleware

_WRITE_TOOLS = frozenset({"mesa_remember", "mesa_improve", "mesa_forget"})
_POLICY_OPERATIONS = {
    "mesa_remember": "WRITE",
    "mesa_improve": "UPDATE",
    "mesa_forget": "DELETE",
}


@dataclass
class _CacheEntry:
    value: dict[str, Any]
    expires_at: float


class CircuitBreaker:
    """Small async circuit breaker for the gateway-to-MESA seam."""

    def __init__(self, failure_threshold: int = 5, recovery_seconds: float = 10.0):
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "CLOSED"
        if time.monotonic() - self._opened_at >= self._recovery_seconds:
            return "HALF_OPEN"
        return "OPEN"

    async def call(
        self, operation: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        if self.state == "OPEN":
            raise MCPError(
                "BACKEND_UNAVAILABLE", "MESA circuit is open", retryable=True
            )
        try:
            result = await operation()
        except MCPError as exc:
            if exc.retryable:
                self._failures += 1
                if self._failures >= self._failure_threshold:
                    self._opened_at = time.monotonic()
            raise
        else:
            self._failures = 0
            self._opened_at = None
            return result


class GatewayOperationService:
    """Coordinates policy, durable operations and V4 without leaking them to callers."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        middleware: ControlPlaneMiddleware,
        v4_service: MesaHttpV4Service,
        encryption_key: str,
    ) -> None:
        self._engine = engine
        self._middleware = middleware
        self._v4 = v4_service
        self._cipher = Fernet(encryption_key.encode())
        self._breaker = CircuitBreaker()
        self._recall_cache: dict[str, _CacheEntry] = {}
        self._inflight_recalls: dict[str, asyncio.Task[dict[str, Any]]] = {}

    async def handshake(
        self, *, client_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        fingerprint = _required(payload, "workspace_fingerprint")
        binding = await self._middleware.client_repo.get_project_binding(
            client_id, fingerprint
        )
        if binding is None:
            raise MCPError("ACCESS_DENIED", "workspace is not bound for this client")
        connection_id = f"conn_{uuid.uuid4().hex}"
        await self._middleware.conn_repo.register_connection(
            connection_id,
            client_id,
            "HTTP",
            status="READY",
            protocol_version=str(payload.get("mcp_protocol_version", "unknown")),
            client_version=str(payload.get("bridge_version", "unknown")),
            project_id=fingerprint,
        )
        return {
            "connection_id": connection_id,
            "status": "READY",
            "heartbeat_interval_seconds": 20,
            "capabilities": {
                "read": True,
                "write": "require_approval",
                "delete": "require_approval",
                "max_context_tokens": 4000,
            },
        }

    async def handshake_for_principal(
        self, *, principal: GatewayPrincipal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Bind a bridge connection to the credential's one durable scope."""
        fingerprint = _required(payload, "workspace_fingerprint")
        binding, _client = await self._scope_for_principal(principal)
        if binding["external_project_id"] != fingerprint:
            raise MCPError("ACCESS_DENIED", "workspace is not bound to credential")
        instance_id = _required(payload, "client_instance_id")
        connection_id = f"conn_{uuid.uuid4().hex}"
        await self._middleware.conn_repo.register_connection(
            connection_id,
            principal.client_id,
            "STDIO",
            status="READY",
            protocol_version=str(payload.get("mcp_protocol_version", "unknown")),
            client_version=str(payload.get("bridge_version", "unknown")),
            session_id=instance_id,
            project_id=fingerprint,
        )
        return {
            "connection_id": connection_id,
            "status": "READY",
            "heartbeat_interval_seconds": 20,
            "capabilities": {
                "read": True,
                "write": "require_approval",
                "delete": "require_approval",
                "max_context_tokens": 2500,
            },
        }

    async def health(self) -> dict[str, Any]:
        gateway = "HEALTHY"
        api = "HEALTHY"
        try:
            await self._breaker.call(self._v4.health)
        except MCPError:
            api = "UNAVAILABLE"
        status = "READY" if api == "HEALTHY" else "DEGRADED"
        return {
            "status": status,
            "components": {
                "gateway": gateway,
                "mesa_api": api,
                "circuit_breaker": self._breaker.state,
            },
            "capabilities": {
                "read": api == "HEALTHY",
                "write_queue": True,
                "delete": api == "HEALTHY",
            },
        }

    async def call_tool(
        self,
        *,
        client_id: str,
        connection_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        binding, client = await self._scope(client_id, connection_id)
        if tool_name == "mesa_health":
            return await self.health()
        if tool_name == "mesa_get_operation_status":
            return await self.operation_status(
                client_id, _required(arguments, "operation_id")
            )
        if tool_name == "mesa_recall":
            return await self._recall(binding, client, arguments)
        if tool_name not in _WRITE_TOOLS:
            raise MCPError("NOT_FOUND", "unknown MCP tool")
        idempotency_key = _required(arguments, "idempotency_key")
        operation = await self._create_operation(
            client_id, binding, connection_id, tool_name, idempotency_key, arguments
        )
        if operation["status"] not in {"CREATED", "APPROVED"}:
            return _operation_response(operation)
        effect = await self._middleware.policy_engine.evaluate(
            client_id, binding["external_project_id"], _POLICY_OPERATIONS[tool_name]
        )
        if effect == "DENY":
            await self._set_operation(
                operation["operation_id"], "DENIED", error_code="DENIED"
            )
            return await self.operation_status(client_id, operation["operation_id"])
        if effect == "REQUIRE_APPROVAL":
            approval_id = f"apr_{uuid.uuid4().hex}"
            await self._middleware.approval_repo.create_approval_request(
                approval_id=approval_id,
                call_id=operation["operation_id"],
                client_id=client_id,
                operation=_POLICY_OPERATIONS[tool_name],
                request_summary=f"{tool_name} requested via Antigravity",
                payload_hash=operation["payload_hash"],
                payload_encrypted=operation["payload_encrypted"],
            )
            await self._set_operation(
                operation["operation_id"], "PENDING_APPROVAL", approval_id=approval_id
            )
            return await self.operation_status(client_id, operation["operation_id"])
        return await self._run_operation(operation, binding, client)

    async def process_approved_operations(self) -> int:
        """Execute dashboard-approved work after a restart or disconnected bridge."""
        async with self._engine.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                "SELECT * FROM mcp_operations WHERE status = 'PENDING_APPROVAL'"
            ) as cursor:
                operations = [dict(row) for row in await cursor.fetchall()]
        completed = 0
        for operation in operations:
            approval = await self._middleware.approval_repo.get_approval_request(
                str(operation["approval_id"])
            )
            if approval is None or approval["status"] == "PENDING":
                continue
            if approval["status"] != "APPROVED":
                await self._set_operation(operation["operation_id"], "DENIED")
                completed += 1
                continue
            binding, client = await self._scope_for_operation(operation)
            await self._set_operation(operation["operation_id"], "APPROVED")
            await self._run_operation(operation, binding, client)
            completed += 1
        return completed

    async def operation_status(
        self, client_id: str, operation_id: str
    ) -> dict[str, Any]:
        operation = await self._get_operation(operation_id)
        if operation is None or operation["client_id"] != client_id:
            raise MCPError("NOT_FOUND", "operation was not found")
        return _operation_response(operation)

    async def operation_status_for_principal(
        self, principal: GatewayPrincipal, operation_id: str
    ) -> dict[str, Any]:
        operation = await self._get_operation(operation_id)
        if (
            operation is None
            or operation["client_id"] != principal.client_id
            or operation["binding_id"] != principal.binding_id
        ):
            raise MCPError("NOT_FOUND", "operation was not found")
        return _operation_response(operation)

    async def call_tool_for_principal(
        self, *, principal: GatewayPrincipal, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch a direct MCP request without trusting caller scope fields."""
        binding, client = await self._scope_for_principal(principal)
        if tool_name == "mesa_health":
            return await self.health()
        if tool_name == "mesa_get_operation_status":
            return await self.operation_status_for_principal(
                principal, _required(arguments, "operation_id")
            )
        if tool_name == "mesa_recall":
            return await self._recall(binding, client, arguments)
        if tool_name not in _WRITE_TOOLS:
            raise MCPError("NOT_FOUND", "unknown MCP tool")
        idempotency_key = _required(arguments, "idempotency_key")
        operation = await self._create_operation(
            principal.client_id,
            binding,
            f"credential:{principal.credential_id}",
            tool_name,
            idempotency_key,
            arguments,
        )
        if operation["status"] not in {"CREATED", "APPROVED"}:
            return _operation_response(operation)
        effect = await self._middleware.policy_engine.evaluate(
            principal.client_id,
            binding["external_project_id"],
            _POLICY_OPERATIONS[tool_name],
        )
        if effect == "DENY":
            await self._set_operation(
                operation["operation_id"], "DENIED", error_code="DENIED"
            )
            return await self.operation_status_for_principal(
                principal, operation["operation_id"]
            )
        if effect == "REQUIRE_APPROVAL":
            approval_id = f"apr_{uuid.uuid4().hex}"
            await self._middleware.approval_repo.create_approval_request(
                approval_id=approval_id,
                call_id=operation["operation_id"],
                client_id=principal.client_id,
                operation=_POLICY_OPERATIONS[tool_name],
                request_summary=f"{tool_name} requested via Codex",
                payload_hash=operation["payload_hash"],
                payload_encrypted=operation["payload_encrypted"],
            )
            await self._set_operation(
                operation["operation_id"], "PENDING_APPROVAL", approval_id=approval_id
            )
            return await self.operation_status_for_principal(
                principal, operation["operation_id"]
            )
        return await self._run_operation(operation, binding, client)

    async def _scope(
        self, client_id: str, connection_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        connection = await self._middleware.conn_repo.get_connection(connection_id)
        if (
            connection is None
            or connection["client_id"] != client_id
            or connection["status"] == "REVOKED"
        ):
            raise MCPError("ACCESS_DENIED", "connection is not active")
        binding = await self._middleware.client_repo.get_project_binding(
            client_id, str(connection["project_id"])
        )
        client = await self._middleware.client_repo.get_client(client_id)
        if binding is None or client is None or not client.get("enabled", True):
            raise MCPError("ACCESS_DENIED", "client scope is unavailable")
        return binding, client

    async def _scope_for_principal(
        self, principal: GatewayPrincipal
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        binding = await self._middleware.client_repo.get_project_binding_by_id(
            principal.binding_id
        )
        client = await self._middleware.client_repo.get_client(principal.client_id)
        if (
            binding is None
            or binding["client_id"] != principal.client_id
            or client is None
            or not client.get("enabled", True)
        ):
            raise MCPError("ACCESS_DENIED", "credential scope is unavailable")
        return binding, client

    async def _scope_for_operation(
        self, operation: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        async with self._engine.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                "SELECT * FROM mcp_project_bindings WHERE binding_id = ? AND enabled = 1",
                (operation["binding_id"],),
            ) as cursor:
                row = await cursor.fetchone()
        client = await self._middleware.client_repo.get_client(operation["client_id"])
        if row is None or client is None or not client.get("enabled", True):
            raise MCPError("ACCESS_DENIED", "operation scope is no longer active")
        return dict(row), client

    async def _recall(
        self, binding: dict[str, Any], client: dict[str, Any], arguments: dict[str, Any]
    ) -> dict[str, Any]:
        query = _required(arguments, "query")
        mode = arguments.get("mode", "search")
        profile = await self._middleware.codex_profile_repo.get(binding["binding_id"])
        default_limit = profile["max_records"] if mode == "context" else 8
        default_budget = profile["max_tokens"] if mode == "context" else 2500
        limit = min(max(int(arguments.get("limit", default_limit)), 1), 8)
        token_budget = min(
            max(int(arguments.get("token_budget", default_budget)), 1), 2500
        )
        include_types = arguments.get("memory_types")
        if include_types is None and mode == "context":
            include_types = profile["memory_types"]
        if include_types is not None and (
            not isinstance(include_types, list)
            or any(not isinstance(item, str) for item in include_types)
        ):
            raise MCPError(
                "INVALID_ARGUMENT", "memory_types must be an array of strings"
            )
        policy_version = await self._policy_cache_version()
        key = hashlib.sha256(
            json.dumps(
                [
                    binding["binding_id"],
                    query.strip().casefold(),
                    mode,
                    limit,
                    token_budget,
                    sorted(include_types or []),
                    policy_version,
                    profile["revision"],
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        cached = self._recall_cache.get(key)
        if cached and cached.expires_at > time.monotonic():
            return {**cached.value, "cache_status": "HIT"}
        task = self._inflight_recalls.get(key)
        if task is None:
            task = asyncio.create_task(
                self._fetch_recall(
                    binding, client, query, limit, mode, token_budget, include_types
                )
            )
            self._inflight_recalls[key] = task
        try:
            result = await task
        finally:
            if task.done():
                self._inflight_recalls.pop(key, None)
        self._recall_cache[key] = _CacheEntry(result, time.monotonic() + 45.0)
        return {**result, "cache_status": "MISS"}

    async def _fetch_recall(
        self,
        binding: dict[str, Any],
        client: dict[str, Any],
        query: str,
        limit: int,
        mode: str,
        token_budget: int,
        include_types: list[str] | None,
    ) -> dict[str, Any]:
        results = await self._breaker.call(
            lambda: self._v4.v4_recall(
                tenant_id=binding["tenant_id"],
                workspace_id=binding["workspace_id"],
                dataset_id=binding["dataset_id"],
                actor_id=client["principal_id"],
                query=query,
                limit=limit,
            )
        )
        memories = results if isinstance(results, list) else []
        if include_types is not None:
            memories = [
                memory
                for memory in memories
                if memory.get("memory_type") in include_types
            ]
        if mode != "context":
            return {"status": "SUCCESS", "memories": memories, "total": len(memories)}
        seen: set[str] = set()
        packed: list[dict[str, Any]] = []
        remaining = token_budget * 4
        for memory in memories:
            content = str(memory.get("content") or "")
            digest = hashlib.sha256(content.encode()).hexdigest()
            if not content or digest in seen or len(content) > remaining:
                continue
            seen.add(digest)
            packed.append(memory)
            remaining -= len(content)
        context_text = "\n\n".join(
            f"[{memory.get('memory_type', 'unknown')}:{memory.get('memory_id', 'unknown')}]\n{memory.get('content', '')}"
            for memory in packed
        )
        return {
            "status": "SUCCESS",
            "context_text": context_text,
            "memories": packed,
            "estimated_tokens": (token_budget * 4 - remaining + 3) // 4,
            "truncated": len(packed) < len(memories),
        }

    async def _create_operation(
        self,
        client_id: str,
        binding: dict[str, Any],
        connection_id: str,
        tool_name: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        async with self._engine.transaction() as db:
            await db.execute(
                "INSERT OR IGNORE INTO mcp_operations (operation_id, client_id, binding_id, connection_id, tool_name, idempotency_key, payload_hash, payload_encrypted, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CREATED')",
                (
                    f"op_{uuid.uuid4().hex}",
                    client_id,
                    binding["binding_id"],
                    connection_id,
                    tool_name,
                    idempotency_key,
                    digest,
                    self._cipher.encrypt(canonical.encode()),
                ),
            )
            async with db.execute(
                "SELECT * FROM mcp_operations WHERE client_id = ? AND binding_id = ? AND tool_name = ? AND idempotency_key = ?",
                (client_id, binding["binding_id"], tool_name, idempotency_key),
            ) as cursor:
                row = await cursor.fetchone()
            await db.commit()
        if row is None:
            raise RuntimeError("operation ledger insert did not persist")
        operation = dict(row)
        if operation["payload_hash"] != digest:
            raise MCPError("INVALID_ARGUMENT", "idempotency_key payload mismatch")
        return operation

    async def _run_operation(
        self, operation: dict[str, Any], binding: dict[str, Any], client: dict[str, Any]
    ) -> dict[str, Any]:
        payload = json.loads(
            self._cipher.decrypt(operation["payload_encrypted"]).decode()
        )
        scope = {
            "tenant_id": binding["tenant_id"],
            "workspace_id": binding["workspace_id"],
            "dataset_id": binding["dataset_id"],
            "actor_id": client["principal_id"],
            "idempotency_key": operation["idempotency_key"],
        }
        try:
            if operation["tool_name"] == "mesa_remember":
                response = await self._breaker.call(
                    lambda: self._v4.v4_remember(
                        **scope,
                        content=_required(payload, "content"),
                        title=payload.get("title"),
                        metadata=payload.get("metadata", {}),
                    )
                )
            elif operation["tool_name"] == "mesa_improve":
                response = await self._breaker.call(
                    lambda: self._v4.v4_improve(
                        **scope,
                        document_id=_required(payload, "document_id"),
                        content=_required(payload, "content"),
                    )
                )
            else:
                response = await self._breaker.call(
                    lambda: self._v4.v4_forget(
                        **scope, document_id=_required(payload, "document_id")
                    )
                )
        except MCPError as exc:
            await self._set_operation(
                operation["operation_id"],
                "FAILED",
                error_code=exc.code,
                error_message=exc.message,
            )
            raise
        mutation_id = (
            response.get("mutation_id") if isinstance(response, dict) else None
        )
        await self._set_operation(
            operation["operation_id"],
            "ACCEPTED",
            mutation_id=mutation_id,
            response=response,
        )
        self._invalidate_recall_cache(binding["binding_id"])
        return await self.operation_status(
            operation["client_id"], operation["operation_id"]
        )

    async def _set_operation(
        self,
        operation_id: str,
        status: str,
        *,
        approval_id: str | None = None,
        mutation_id: str | None = None,
        response: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        terminal = status in {"ACCEPTED", "DENIED", "FAILED"}
        async with self._engine.transaction() as db:
            await db.execute(
                "UPDATE mcp_operations SET status = ?, approval_id = COALESCE(?, approval_id), mutation_id = COALESCE(?, mutation_id), response_json = COALESCE(?, response_json), error_code = COALESCE(?, error_code), error_message = COALESCE(?, error_message), updated_at = CURRENT_TIMESTAMP, completed_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE completed_at END WHERE operation_id = ?",
                (
                    status,
                    approval_id,
                    mutation_id,
                    json.dumps(response, sort_keys=True) if response else None,
                    error_code,
                    error_message,
                    terminal,
                    operation_id,
                ),
            )
            await db.commit()

    async def _get_operation(self, operation_id: str) -> dict[str, Any] | None:
        async with self._engine.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                "SELECT * FROM mcp_operations WHERE operation_id = ?", (operation_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row else None

    def _invalidate_recall_cache(self, binding_id: str) -> None:
        # Cache values are keyed from a digest, so inspect their binding marker in
        # the value rather than trying to reverse the digest.  Expiring all entries
        # remains bounded (45 seconds) and is safer than serving a stale write.
        self._recall_cache.clear()

    async def _policy_cache_version(self) -> str:
        """Fold durable policy state into recall cache identity."""
        rules = await self._middleware.policy_repo.list_rules()
        defaults = [
            await self._middleware.settings_repo.get_setting("writes.default_policy"),
            await self._middleware.settings_repo.get_setting("deletes.default_policy"),
        ]
        canonical = json.dumps(
            {"rules": rules, "defaults": defaults},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


def _required(arguments: dict[str, Any], field: str) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value.strip():
        raise MCPError("INVALID_ARGUMENT", f"{field} is required")
    return value.strip()


def _operation_response(operation: dict[str, Any]) -> dict[str, Any]:
    result = {
        "operation_id": operation["operation_id"],
        "status": operation["status"],
        "mutation_id": operation.get("mutation_id"),
        "approval_id": operation.get("approval_id"),
    }
    if operation.get("response_json"):
        result["result"] = json.loads(operation["response_json"])
    if operation.get("error_code"):
        result["error"] = {
            "code": operation["error_code"],
            "message": operation.get("error_message"),
        }
    if operation["status"] == "PENDING_APPROVAL":
        result["poll_after_ms"] = 2000
    return result
