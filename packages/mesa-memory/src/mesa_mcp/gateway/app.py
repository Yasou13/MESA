"""Standalone HTTP application for the durable MESA MCP gateway."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine

from ..configuration import MCPSettings
from ..errors import MCPError
from ..v4_service import MesaHttpV4Service
from ..workspace import workspace_fingerprint
from .auth import GatewayAuth
from .codex_transport import CodexStreamableTransport, CodexTransportMiddleware
from .middleware import ControlPlaneMiddleware
from .operations import GatewayOperationService

logger = logging.getLogger(__name__)


def create_gateway_app(settings: MCPSettings | None = None) -> FastAPI:
    settings = settings or MCPSettings()
    if not settings.gateway_encryption_key:
        raise ValueError("MESA_GATEWAY_ENCRYPTION_KEY is required")
    engine = AsyncEngine(str(settings.gateway_control_db))
    middleware = ControlPlaneMiddleware(engine=engine)
    v4_service = MesaHttpV4Service(settings)
    operations = GatewayOperationService(
        engine=engine,
        middleware=middleware,
        v4_service=v4_service,
        encryption_key=settings.gateway_encryption_key,
    )
    auth = GatewayAuth(credential_repo=middleware.credential_repo)
    codex_transport = CodexStreamableTransport(operations, auth)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await engine.initialize()
        await initialize_schema(engine)
        await middleware.initialize()
        async with codex_transport.run():
            task = asyncio.create_task(_approval_loop(operations))
            app.state.operation_service = operations
            try:
                yield
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                await v4_service.close()
                await middleware.close()

    app = FastAPI(title="MESA MCP Gateway", version="0.2.0", lifespan=lifespan)
    app.add_middleware(CodexTransportMiddleware, transport=codex_transport)

    @app.exception_handler(MCPError)
    async def mcp_error(_request: Request, exc: MCPError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": exc.as_dict()["error"]})

    async def bridge_principal(request: Request):
        authorization = request.headers.get("authorization", "")
        token = authorization[7:] if authorization.lower().startswith("bearer ") else ""
        principal = await auth.authenticate_credential(token)
        if principal is None:
            raise HTTPException(status_code=401, detail="Invalid bridge credential")
        return principal

    @app.post("/mcp/v1/handshake")
    async def handshake(request: Request) -> dict[str, Any]:
        return await operations.handshake_for_principal(
            principal=await bridge_principal(request), payload=await request.json()
        )

    @app.post("/mcp/v1/tools/call")
    async def call_tool(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            result = await operations.call_tool_for_principal(
                principal=await bridge_principal(request),
                tool_name=str(payload.get("name", "")),
                arguments=payload.get("arguments") or {},
            )
        except MCPError as exc:
            return {
                "content": [{"type": "text", "text": json.dumps(exc.as_dict())}],
                "isError": True,
            }
        return {
            "content": [{"type": "text", "text": json.dumps(result)}],
            "isError": False,
        }

    @app.get("/mcp/v1/operations/{operation_id}")
    async def operation_status(operation_id: str, request: Request) -> dict[str, Any]:
        return await operations.operation_status_for_principal(
            await bridge_principal(request), operation_id
        )

    @app.get("/mcp/v1/health")
    async def health(request: Request) -> dict[str, Any]:
        await bridge_principal(request)
        return await operations.health()

    @app.post("/mcp/v1/codex/sessions/start")
    async def codex_session_start(request: Request) -> dict[str, Any]:
        principal = await bridge_principal(request)
        payload = await request.json()
        session_id = str(payload.get("session_id", "")).strip()
        fingerprint = str(payload.get("workspace_fingerprint", "")).strip()
        binding = await middleware.client_repo.get_project_binding_by_id(
            principal.binding_id
        )
        if (
            not session_id
            or binding is None
            or binding["external_project_id"] != fingerprint
        ):
            raise HTTPException(
                status_code=403, detail="Workspace is not bound to credential"
            )
        connection_id = "codex_" + hashlib.sha256(session_id.encode()).hexdigest()[:32]
        if await middleware.conn_repo.get_connection(connection_id) is None:
            await middleware.conn_repo.register_connection(
                connection_id,
                principal.client_id,
                "HTTP",
                status="CONNECTED",
                protocol_version="streamable-http",
                client_version="codex",
                session_id=session_id,
                project_id=fingerprint,
            )
        return {
            "connection_id": connection_id,
            "status": "CONNECTED",
            "profile": await middleware.codex_profile_repo.ensure(principal.binding_id),
        }

    @app.post("/mcp/v1/codex/sessions/end")
    async def codex_session_end(request: Request) -> dict[str, str]:
        principal = await bridge_principal(request)
        session_id = str((await request.json()).get("session_id", "")).strip()
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")
        connection_id = "codex_" + hashlib.sha256(session_id.encode()).hexdigest()[:32]
        connection = await middleware.conn_repo.get_connection(connection_id)
        if connection is not None and connection["client_id"] == principal.client_id:
            await middleware.conn_repo.update_connection_status(
                connection_id, "DISCONNECTED"
            )
        return {"connection_id": connection_id, "status": "DISCONNECTED"}

    return app


async def _approval_loop(operations: GatewayOperationService) -> None:
    while True:
        try:
            await operations.process_approved_operations()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("gateway approval worker failed")
        await asyncio.sleep(1)


async def _issue_credential(args: argparse.Namespace) -> str:
    engine = AsyncEngine(str(args.control_db))
    await engine.initialize()
    await initialize_schema(engine)
    middleware = ControlPlaneMiddleware(engine=engine)
    await middleware.initialize()
    try:
        client = await middleware.client_repo.get_client(args.client_id)
        if client is None:
            await middleware.client_repo.create_client(
                args.client_id, args.display_name, "codex", args.principal_id
            )
        elif client["client_type"] != "codex":
            raise ValueError("client_id already belongs to a non-Codex client")
        else:
            await middleware.client_repo.update_client(
                args.client_id,
                display_name=args.display_name,
                principal_id=args.principal_id,
            )
        binding_id = await middleware.client_repo.add_project_binding(
            args.client_id,
            workspace_fingerprint(args.workspace_root),
            args.tenant_id,
            args.workspace_id,
            args.dataset_id,
        )
        _record, token = await middleware.credential_repo.issue(
            args.client_id, binding_id
        )
        return token
    finally:
        await middleware.close()


async def _revoke_credential(args: argparse.Namespace) -> bool:
    engine = AsyncEngine(str(args.control_db))
    await engine.initialize()
    await initialize_schema(engine)
    middleware = ControlPlaneMiddleware(engine=engine)
    await middleware.initialize()
    try:
        return await middleware.credential_repo.revoke(args.credential_id)
    finally:
        await middleware.close()


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mesa-mcp-gateway")
    commands = parser.add_subparsers(dest="command")
    credential = commands.add_parser("credential", help="manage direct MCP credentials")
    credential.add_argument(
        "--control-db", type=Path, default=MCPSettings().gateway_control_db
    )
    credential_commands = credential.add_subparsers(
        dest="credential_command", required=True
    )
    issue = credential_commands.add_parser(
        "issue", help="issue a token shown exactly once"
    )
    issue.add_argument("--client-id", required=True)
    issue.add_argument("--display-name", default="Codex")
    issue.add_argument("--principal-id", required=True)
    issue.add_argument("--workspace-root", type=Path, required=True)
    issue.add_argument("--tenant-id", required=True)
    issue.add_argument("--workspace-id", required=True)
    issue.add_argument("--dataset-id", required=True)
    revoke = credential_commands.add_parser(
        "revoke", help="revoke a direct MCP credential"
    )
    revoke.add_argument("--credential-id", required=True)
    return parser


def main() -> None:
    import uvicorn

    parser = _cli_parser()
    args, unknown = parser.parse_known_args()
    if args.command == "credential":
        if unknown:
            parser.error("unexpected arguments")
        if args.credential_command == "issue":
            print(asyncio.run(_issue_credential(args)))
            return
        if not asyncio.run(_revoke_credential(args)):
            parser.error("active credential not found")
        print(f"revoked:{args.credential_id}")
        return
    uvicorn.run(create_gateway_app(), host="127.0.0.1", port=8765)
