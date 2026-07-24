"""HTTP Gateway for Model Context Protocol."""

import datetime
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from mesa_mcp.adapter import MesaMCPAdapter
from mesa_mcp.gateway.middleware import ControlPlaneMiddleware
from mesa_mcp.server import _tools

from .auth import GatewayAuth
from .heartbeat import HeartbeatMonitor


def create_gateway_router(
    adapter: MesaMCPAdapter,
    auth: GatewayAuth,
    heartbeat: HeartbeatMonitor,
    conn_repo: Any,
    middleware: ControlPlaneMiddleware,
) -> APIRouter:
    router = APIRouter()

    @router.post("/mcp/v1/connect")
    async def connect(request: Request, client_id: str = Depends(auth.authenticate)):
        """Establish a logical connection."""
        conn_id = f"conn_{uuid.uuid4().hex}"
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Register connection in database
        async with conn_repo._sql.transaction() as db:
            await db.execute(
                """
                INSERT INTO mcp_connections (
                    connection_id, client_id, transport_type, protocol_version, status,
                    connected_at, last_heartbeat
                ) VALUES (
                    :conn_id, :client_id, 'HTTP', '2024-11-05', 'CONNECTED',
                    :now, :now
                )
                """,
                {"conn_id": conn_id, "client_id": client_id, "now": now},
            )
            await db.commit()

        return {"connection_id": conn_id, "status": "CONNECTED"}

    @router.post("/mcp/v1/heartbeat")
    async def heartbeat_ping(
        connection_id: str, client_id: str = Depends(auth.authenticate)
    ):
        """Client pings this endpoint to keep connection alive."""
        await heartbeat.record_heartbeat(connection_id)
        return {"status": "ok"}

    @router.get("/mcp/v1/tools/list")
    async def list_tools(client_id: str = Depends(auth.authenticate)):
        tools = _tools()
        return {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.inputSchema,
                }
                for t in tools
            ]
        }

    @router.post("/mcp/v1/tools/call")
    async def call_tool(request: Request, client_id: str = Depends(auth.authenticate)):
        body = await request.json()
        tool_name = body.get("name")
        arguments = body.get("arguments", {})

        # In HTTP Gateway, we map the tool request back to the adapter directly.
        # But wait, we need to pass this through the ControlPlaneMiddleware for tracing!
        # For simplicity in this implementation, we will mock the middleware hook or
        # call the adapter directly if middleware isn't easily accessible.
        # Note: In a real implementation, the middleware should be transport-agnostic.

        try:
            if tool_name == "mesa_health":
                res = await adapter.health()
                return {
                    "content": [{"type": "text", "text": json.dumps(res)}],
                    "isError": False,
                }

            handlers = {
                "mesa_store_memory": adapter.store_memory,
                "mesa_search_memory": adapter.search_memory,
                "mesa_get_memory": adapter.get_memory,
                "mesa_get_context": adapter.get_context,
                "mesa_remember": adapter.mesa_remember,
                "mesa_recall": adapter.mesa_recall,
                "mesa_improve": adapter.mesa_improve,
                "mesa_forget": adapter.mesa_forget,
            }

            if tool_name not in handlers:
                raise HTTPException(status_code=404, detail="Tool not found")

            res = await middleware.execute_tool(
                tool_name, arguments, handlers[tool_name]
            )
            return {
                "content": [{"type": "text", "text": json.dumps(res)}],
                "isError": False,
            }
        except Exception as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}

    return router
