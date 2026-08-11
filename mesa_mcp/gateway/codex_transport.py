"""Authenticated Streamable HTTP MCP transport for Codex clients."""

from __future__ import annotations

import contextvars
import json
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.authentication import AuthCredentials
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from ..errors import MCPError
from ..security import MEMORY_TYPES
from .auth import GatewayAuth, GatewayPrincipal
from .operations import GatewayOperationService

_principal: contextvars.ContextVar[GatewayPrincipal] = contextvars.ContextVar(
    "mesa_codex_principal"
)


class CodexStreamableTransport:
    """Deep transport seam: MCP protocol, auth session binding and error shaping."""

    def __init__(self, operations: GatewayOperationService, auth: GatewayAuth):
        self._operations = operations
        self._auth = auth
        self._server = _create_server(operations)
        self._sessions = StreamableHTTPSessionManager(app=self._server)

    @asynccontextmanager
    async def run(self):
        async with self._sessions.run():
            yield

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await JSONResponse({"detail": "HTTP required"}, status_code=400)(scope, receive, send)
            return
        authorization = _authorization_header(scope)
        principal = await self._auth.authenticate_credential(authorization)
        if principal is None:
            await JSONResponse(
                {"error": "invalid_token", "error_description": "Authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )(scope, receive, send)
            return
        # StreamableHTTPSessionManager natively binds every MCP session to this
        # credential identity.  The context is inherited by the new session task
        # and never comes from tool arguments or arbitrary headers.
        scope["user"] = AuthenticatedUser(
            AccessToken(
                token="",
                client_id=principal.credential_id,
                subject=principal.client_id,
                scopes=["mesa"],
                claims={"binding_id": principal.binding_id},
            )
        )
        scope["auth"] = AuthCredentials(["mesa"])
        context_token = _principal.set(principal)
        try:
            await self._sessions.handle_request(scope, receive, send)
        finally:
            _principal.reset(context_token)


class CodexTransportMiddleware:
    """Route `/mcp` before FastAPI sees it while retaining the app lifespan."""

    def __init__(self, app: Callable[..., Awaitable[None]], transport: CodexStreamableTransport):
        self.app = app
        self.transport = transport

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            await self.transport(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _authorization_header(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name.lower() == b"authorization":
            header = value.decode("latin-1")
            if header.lower().startswith("bearer "):
                return header[7:]
    return ""


def _create_server(operations: GatewayOperationService) -> Server:
    app = Server("mesa-codex-gateway", version="0.3.0")

    @app.list_tools()  # type: ignore[untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        return _tools()

    @app.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        try:
            principal = _principal.get()
            result = await operations.call_tool_for_principal(
                principal=principal, tool_name=name, arguments=arguments or {}
            )
        except MCPError as exc:
            return _result(exc.as_dict(), is_error=True)
        except Exception:
            return _result(
                MCPError("INTERNAL_ERROR", "MESA operation failed").as_dict(),
                is_error=True,
            )
        return _result(result, is_error=False)

    return app


def _result(value: dict[str, Any], *, is_error: bool) -> types.CallToolResult:
    return types.CallToolResult(
        isError=is_error,
        content=[types.TextContent(type="text", text=json.dumps(value, ensure_ascii=False))],
    )


def _tools() -> list[types.Tool]:
    read = types.ToolAnnotations(readOnlyHint=True, idempotentHint=True)
    write = types.ToolAnnotations(readOnlyHint=False, idempotentHint=True)
    destructive = types.ToolAnnotations(readOnlyHint=False, destructiveHint=True)
    return [
        types.Tool(name="mesa_health", description="Check MESA gateway health without exposing credentials.", inputSchema={"type": "object", "properties": {}}, annotations=read),
        types.Tool(
            name="mesa_recall", description="Retrieve binding-scoped MESA memories and optional token-bounded context.",
            inputSchema={"type": "object", "properties": {"query": {"type": "string", "maxLength": 2000}, "mode": {"type": "string", "enum": ["search", "context"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 8}, "token_budget": {"type": "integer", "minimum": 1, "maximum": 2500}, "memory_types": {"type": "array", "items": {"type": "string", "enum": sorted(MEMORY_TYPES)}}}, "required": ["query"]},
            annotations=read,
        ),
        types.Tool(name="mesa_remember", description="Create a durable V4 memory after MESA policy approval.", inputSchema={"type": "object", "properties": {"content": {"type": "string", "maxLength": 20000}, "title": {"type": "string"}, "metadata": {"type": "object"}, "source_ref": {"type": "string", "maxLength": 2048}, "evidence_span": {"type": "string", "maxLength": 4096}, "memory_type": {"type": "string", "enum": sorted(MEMORY_TYPES)}, "importance": {"type": "number", "minimum": 0, "maximum": 1}, "idempotency_key": {"type": "string", "maxLength": 128}}, "required": ["content", "idempotency_key"]}, annotations=write),
        types.Tool(name="mesa_improve", description="Revise a durable V4 memory after MESA policy approval.", inputSchema={"type": "object", "properties": {"document_id": {"type": "string"}, "content": {"type": "string", "maxLength": 20000}, "metadata": {"type": "object"}, "supersedes_revision_id": {"type": "string", "maxLength": 256}, "idempotency_key": {"type": "string", "maxLength": 128}}, "required": ["document_id", "content", "idempotency_key"]}, annotations=write),
        types.Tool(name="mesa_forget", description="Purge a durable V4 memory after explicit approval.", inputSchema={"type": "object", "properties": {"document_id": {"type": "string"}, "idempotency_key": {"type": "string", "maxLength": 256}}, "required": ["document_id", "idempotency_key"]}, annotations=destructive),
        types.Tool(name="mesa_get_operation_status", description="Read the durable status of a prior MESA mutation.", inputSchema={"type": "object", "properties": {"operation_id": {"type": "string"}}, "required": ["operation_id"]}, annotations=read),
    ]
