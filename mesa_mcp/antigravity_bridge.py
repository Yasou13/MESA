"""Small stdio MCP bridge for Antigravity.

The bridge deliberately owns no MESA policy or storage decisions.  It keeps a
durable encrypted write spool while the independent gateway owns operations.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shlex
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import mcp.types as types
from cryptography.fernet import Fernet
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .configuration import MCPSettings
from .errors import MCPError
from .workspace import workspace_fingerprint


class EncryptedWriteSpool:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        self._key_path = root / "antigravity-spool.key"
        if not self._key_path.exists():
            fd = os.open(self._key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(Fernet.generate_key())
        os.chmod(self._key_path, 0o600)
        self._cipher = Fernet(self._key_path.read_bytes())
        self._db = sqlite3.connect(root / "antigravity-spool.db")
        os.chmod(root / "antigravity-spool.db", 0o600)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS outbound_operations ("
            "operation_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, tool_name TEXT NOT NULL, "
            "payload_encrypted BLOB NOT NULL, status TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0, "
            "next_attempt_at REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL, completed_at REAL, last_error TEXT)"
        )
        self._db.commit()

    def enqueue(
        self, tool_name: str, idempotency_key: str, arguments: dict[str, Any]
    ) -> str:
        operation_id = f"local_{uuid.uuid4().hex}"
        payload = self._cipher.encrypt(json.dumps(arguments, sort_keys=True).encode())
        self._db.execute(
            "INSERT OR IGNORE INTO outbound_operations (operation_id, idempotency_key, tool_name, payload_encrypted, status, created_at) VALUES (?, ?, ?, ?, 'QUEUED', ?)",
            (operation_id, idempotency_key, tool_name, payload, time.time()),
        )
        row = self._db.execute(
            "SELECT operation_id FROM outbound_operations WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        self._db.commit()
        return str(row[0])

    def pending(self) -> list[tuple[str, str, str, dict[str, Any], int]]:
        rows = self._db.execute(
            "SELECT operation_id, idempotency_key, tool_name, payload_encrypted, attempt_count FROM outbound_operations WHERE status = 'QUEUED' AND next_attempt_at <= ? ORDER BY created_at",
            (time.time(),),
        ).fetchall()
        return [
            (
                str(row[0]),
                str(row[1]),
                str(row[2]),
                json.loads(self._cipher.decrypt(row[3]).decode()),
                int(row[4]),
            )
            for row in rows
        ]

    def complete(self, operation_id: str) -> None:
        self._db.execute(
            "UPDATE outbound_operations SET status = 'COMPLETED', completed_at = ? WHERE operation_id = ?",
            (time.time(), operation_id),
        )
        self._db.commit()

    def retry(self, operation_id: str, attempts: int, message: str) -> None:
        # 0.2–1.8 seconds plus a deterministic per-operation jitter ceiling.
        delay = min(30.0, 0.2 * (2**attempts) + (hash(operation_id) & 255) / 255)
        self._db.execute(
            "UPDATE outbound_operations SET attempt_count = ?, next_attempt_at = ?, last_error = ? WHERE operation_id = ?",
            (attempts + 1, time.time() + delay, message[:240], operation_id),
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()


class GatewayClient:
    def __init__(
        self,
        settings: MCPSettings,
        workspace_fingerprint: str,
        credential_token: str,
        client_instance_id: str,
    ) -> None:
        if not settings.gateway_url:
            raise ValueError("MESA_GATEWAY_URL is required")
        self._settings = settings
        self._fingerprint = workspace_fingerprint
        self._credential_token = credential_token
        self._client_instance_id = client_instance_id
        self._client = httpx.AsyncClient(
            base_url=settings.gateway_url.rstrip("/"), timeout=httpx.Timeout(8.0)
        )
        self._connection_id: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._credential_token}"}

    async def _connect(self) -> None:
        if self._connection_id:
            return
        response = await self._request(
            "POST",
            "/mcp/v1/handshake",
            headers=self.headers,
            json={
                "client_instance_id": self._client_instance_id,
                "bridge_version": "0.3.0",
                "mcp_protocol_version": "2025-03-26",
                "workspace_fingerprint": self._fingerprint,
                "supported_features": [
                    "approvals",
                    "idempotency",
                    "offline_spool",
                    "typed_context",
                ],
            },
        )
        self._raise_for_response(response)
        self._connection_id = str(response.json()["connection_id"])

    async def health(self) -> dict[str, Any]:
        response = await self._request("GET", "/mcp/v1/health", headers=self.headers)
        self._raise_for_response(response)
        return response.json()

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await self._connect()
        response = await self._request(
            "POST",
            "/mcp/v1/tools/call",
            headers=self.headers,
            json={
                "connection_id": self._connection_id,
                "name": tool_name,
                "arguments": arguments,
            },
        )
        self._raise_for_response(response)
        payload = response.json()
        if payload.get("isError"):
            error = json.loads(payload["content"][0]["text"])["error"]
            raise MCPError(
                error["code"], error["message"], bool(error.get("retryable"))
            )
        return json.loads(payload["content"][0]["text"])

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise MCPError(
                "BACKEND_UNAVAILABLE", "MESA gateway is unavailable", retryable=True
            ) from exc

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _raise_for_response(response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            status = response.status_code
            if status in {400, 401, 403, 404}:
                code = {
                    400: "INVALID_ARGUMENT",
                    401: "ACCESS_DENIED",
                    403: "ACCESS_DENIED",
                    404: "NOT_FOUND",
                }[status]
                raise MCPError(code, "gateway rejected bridge request", retryable=False) from exc
            raise MCPError(
                "BACKEND_UNAVAILABLE",
                "MESA gateway is unavailable",
                retryable=status in {408, 429} or status >= 500,
            ) from exc


def create_mcp_server(
    settings: MCPSettings | None = None,
) -> tuple[Server, GatewayClient, EncryptedWriteSpool]:
    settings = settings or MCPSettings()
    fingerprint = workspace_fingerprint(settings.workspace_root)
    root = (
        Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
        / "mesa"
    )
    spool = EncryptedWriteSpool(root)
    gateway = GatewayClient(
        settings,
        fingerprint,
        _credential_token(fingerprint),
        _persistent_instance_id(root),
    )
    app = Server("mesa-antigravity-bridge")

    @app.list_tools()  # type: ignore[untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        return _tools()

    @app.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> types.CallToolResult:
        arguments = dict(arguments or {})
        try:
            if name == "mesa_health":
                try:
                    health = await gateway.health()
                except MCPError:
                    health = {
                        "status": "DEGRADED",
                        "components": {
                            "bridge": "HEALTHY",
                            "gateway": "UNAVAILABLE",
                            "local_spool": "HEALTHY",
                        },
                        "capabilities": {
                            "read": False,
                            "write_queue": True,
                            "delete": False,
                        },
                    }
                return _success(health)
            await _drain_spool(spool, gateway)
            if name not in {tool.name for tool in _tools()}:
                raise MCPError("NOT_FOUND", "unknown MCP tool")
            if name == "mesa_get_operation_status":
                result = await gateway.call(name, arguments)
                return _success(result)
            if name == "mesa_recall":
                return _success(await gateway.call(name, arguments))
            idempotency_key = _required_idempotency_key(arguments)
            try:
                return _success(await gateway.call(name, arguments))
            except MCPError as exc:
                if exc.retryable and name in {"mesa_remember", "mesa_improve"}:
                    operation_id = spool.enqueue(name, idempotency_key, arguments)
                    return _success(
                        {
                            "ok": True,
                            "status": "QUEUED_OFFLINE",
                            "operation_id": operation_id,
                            "message": "Memory was queued locally and will be synchronized.",
                        }
                    )
                raise
        except MCPError as exc:
            return _error(exc)
        except Exception:
            logging = __import__("logging").getLogger(__name__)
            logging.exception("bridge tool failed", extra={"tool": name})
            return _error(MCPError("INTERNAL_ERROR", "MESA bridge operation failed"))

    return app, gateway, spool


async def _drain_spool(spool: EncryptedWriteSpool, gateway: GatewayClient) -> None:
    for (
        operation_id,
        idempotency_key,
        tool_name,
        arguments,
        attempts,
    ) in spool.pending():
        try:
            arguments["idempotency_key"] = idempotency_key
            await gateway.call(tool_name, arguments)
        except MCPError as exc:
            if exc.retryable:
                spool.retry(operation_id, attempts, exc.message)
            continue
        spool.complete(operation_id)


def _tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="mesa_health",
            description="Return bridge and MESA readiness.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="mesa_recall",
            description="Recall scoped MESA memory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["search", "context"]},
                    "limit": {"type": "integer"},
                    "token_budget": {"type": "integer"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="mesa_remember",
            description="Request durable memory storage.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "title": {"type": "string"},
                    "metadata": {"type": "object"},
                    "idempotency_key": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 256,
                    },
                },
                "required": ["content", "idempotency_key"],
            },
        ),
        types.Tool(
            name="mesa_improve",
            description="Request a memory revision.",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "content": {"type": "string"},
                    "idempotency_key": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 256,
                    },
                },
                "required": ["document_id", "content", "idempotency_key"],
            },
        ),
        types.Tool(
            name="mesa_forget",
            description="Request memory deletion.",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "idempotency_key": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 256,
                    },
                },
                "required": ["document_id", "idempotency_key"],
            },
        ),
        types.Tool(
            name="mesa_get_operation_status",
            description="Poll a durable write operation.",
            inputSchema={
                "type": "object",
                "properties": {"operation_id": {"type": "string"}},
                "required": ["operation_id"],
            },
        ),
    ]


def _success(value: dict[str, Any]) -> types.CallToolResult:
    return types.CallToolResult(
        content=[
            types.TextContent(type="text", text=json.dumps(value, ensure_ascii=False))
        ],
        isError=False,
    )


def _error(error: MCPError) -> types.CallToolResult:
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text=json.dumps(error.as_dict(), ensure_ascii=False)
            )
        ],
        isError=True,
    )


def _credential_token(fingerprint: str) -> str:
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = Path(
        os.environ.get(
            "MESA_ANTIGRAVITY_CREDENTIAL_FILE",
            config_root / "mesa" / "antigravity" / f"{fingerprint}.env",
        )
    )
    if not path.exists() or os.stat(path).st_mode & 0o077:
        raise ValueError("protected Antigravity credential file is unavailable")
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if key == "MESA_ANTIGRAVITY_MCP_TOKEN" and separator:
            parsed = shlex.split(value)
            if parsed and parsed[0]:
                return parsed[0]
    raise ValueError("Antigravity credential token is unavailable")


def _persistent_instance_id(root: Path) -> str:
    path = root / "antigravity-client-instance-id"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("agi_" + secrets.token_hex(16))
    os.chmod(path, 0o600)
    return path.read_text(encoding="utf-8").strip()


def _required_idempotency_key(arguments: dict[str, Any]) -> str:
    value = arguments.get("idempotency_key")
    if not isinstance(value, str) or not value.strip():
        raise MCPError("INVALID_ARGUMENT", "idempotency_key is required")
    return value.strip()


async def _run() -> None:
    app, gateway, spool = create_mcp_server()
    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream, write_stream, app.create_initialization_options()
            )
    finally:
        await gateway.close()
        spool.close()


def main() -> None:
    asyncio.run(_run())
