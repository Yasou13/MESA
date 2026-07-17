import asyncio
import os
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from mesa_api.schemas import MemoryInsertRequest, MemorySearchRequest
from mesa_client.client import AsyncMesaClient

# Create an MCP server instance
app: Server = Server("mesa-mcp")

# Environment variables for MESA configuration
MESA_BASE_URL = os.getenv("MESA_BASE_URL", "http://localhost:8000/v3")
MESA_API_KEY = os.getenv("MESA_API_KEY")
MESA_AGENT_ID = os.getenv("MESA_AGENT_ID")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    """Expose MESA memory functions as MCP tools."""
    return [
        types.Tool(
            name="record_memory",
            description="Record a new piece of information into the MESA memory layer for long-term retention.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session or conversation identifier",
                    },
                    "content": {
                        "type": "string",
                        "description": "The actual information or memory to store",
                    },
                },
                "required": ["session_id", "content"],
            },
        ),
        types.Tool(
            name="search_memory",
            description="Search the MESA memory layer for relevant past information.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session or conversation identifier",
                    },
                    "query": {
                        "type": "string",
                        "description": "The search query or keyword",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to retrieve (default 5)",
                    },
                },
                "required": ["session_id", "query"],
            },
        ),
        types.Tool(
            name="forget_memory",
            description="Permanently delete a memory or all memories for an entity (GDPR/KVKK right-to-erasure)",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Optional session to purge. If omitted, purges entire agent memory.",
                    },
                },
            },
        ),
        types.Tool(
            name="get_stats",
            description="Return memory statistics: total nodes, edges, recent admit/discard rates",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@app.call_tool()
async def call_tool(
    name: str, arguments: dict[str, Any] | None
) -> list[types.TextContent]:
    """Handle execution of MESA tools."""
    if arguments is None:
        raise ValueError("Missing tool arguments")

    agent_id = MESA_AGENT_ID
    if not agent_id:
        return [
            types.TextContent(
                type="text",
                text="Error: MESA_AGENT_ID environment variable is not configured.",
            )
        ]

    session_id = arguments.get("session_id")

    try:
        async with AsyncMesaClient(
            base_url=MESA_BASE_URL, api_key=MESA_API_KEY
        ) as client:
            if name == "record_memory":
                if not session_id:
                    return [
                        types.TextContent(
                            type="text",
                            text="Error: session_id is required for record_memory.",
                        )
                    ]
                content = arguments.get("content")
                if not content:
                    return [
                        types.TextContent(
                            type="text",
                            text="Error: content is required for record_memory.",
                        )
                    ]

                request = MemoryInsertRequest(
                    agent_id=agent_id, session_id=session_id, content=content
                )

                response = await client.insert(request)

                return [
                    types.TextContent(
                        type="text",
                        text=(
                            f"Memory successfully recorded. Status: {response.status}. "
                            f"Node ID: {response.node_id}"
                        ),
                    )
                ]

            elif name == "search_memory":
                if not session_id:
                    return [
                        types.TextContent(
                            type="text",
                            text="Error: session_id is required for search_memory.",
                        )
                    ]
                query = arguments.get("query")
                limit = arguments.get("limit", 5)
                if not query:
                    return [
                        types.TextContent(
                            type="text",
                            text="Error: query is required for search_memory.",
                        )
                    ]

                req = MemorySearchRequest(
                    agent_id=agent_id,
                    session_id=session_id,
                    query=query,
                    limit=limit,
                )

                resp = await client.search(req)

                if not resp.retrieved_nodes:
                    return [
                        types.TextContent(
                            type="text", text="No relevant memories found."
                        )
                    ]

                formatted_results = []
                for i, res in enumerate(resp.retrieved_nodes, 1):
                    content = res.content_payload or res.entity_name
                    formatted_results.append(
                        f"Result {i} (Score: {res.score:.4f}):\n{content}"
                    )

                result_text = "\n\n".join(formatted_results)
                return [
                    types.TextContent(
                        type="text",
                        text=f"Found {len(resp.retrieved_nodes)} results:\n\n{result_text}",
                    )
                ]

            elif name == "forget_memory":
                from mesa_api.schemas import MemoryPurgeRequest

                target_session = arguments.get("session_id")
                purge_req = MemoryPurgeRequest(
                    agent_id=agent_id,
                    scope_id=target_session if target_session else agent_id,
                    scope="session" if target_session else "agent",
                )
                purge_resp = await client.purge(purge_req)
                return [
                    types.TextContent(
                        type="text",
                        text=f"Purge complete. Affected nodes: {purge_resp.records_affected}",
                    )
                ]

            elif name == "get_stats":
                from mesa_storage.dao import MemoryDAO
                from mesa_storage.kuzu_provider import KuzuGraphProvider
                from mesa_storage.sqlite_engine import AsyncEngine

                db_path = os.path.join(
                    os.environ.get("MESA_STORAGE_PATH", "./storage"), "mesa.db"
                )
                kuzu_path = os.path.join(
                    os.environ.get("MESA_STORAGE_PATH", "./storage"), "kuzu"
                )

                sql = AsyncEngine(db_path=db_path)
                await sql.initialize()

                stats = {"total_nodes": 0, "total_edges": 0, "telemetry": {}}

                async with sql.connection() as db:
                    async with db.execute(
                        "SELECT count(*) FROM nodes WHERE agent_id = ?", (agent_id,)
                    ) as cur:
                        row = await cur.fetchone()
                        stats["total_nodes"] = row[0] if row else 0

                dao = MemoryDAO(
                    sqlite_engine=sql, vector_engine=None, graph_provider=None  # type: ignore
                )
                stats["telemetry"] = await dao.get_recent_telemetry_stats(
                    agent_id=agent_id, limit=100
                )
                await sql.close()

                graph = KuzuGraphProvider(db_path=kuzu_path)
                await graph.initialize()
                try:
                    graph_res = await graph.execute_query(
                        "MATCH ()-[r]->() RETURN count(r) as c"
                    )
                    if graph_res and graph_res[0]:
                        stats["total_edges"] = graph_res[0][0]
                except Exception:
                    pass
                finally:
                    await graph.close()

                return [types.TextContent(type="text", text=f"Stats: {stats}")]

            else:
                raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        return [types.TextContent(type="text", text=f"MESA API Error: {str(e)}")]


async def main() -> None:
    """Run the MCP server using standard IO transport."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
