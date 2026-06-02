import asyncio
import os
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from mesa_api.schemas import MemoryInsertRequest, MemorySearchRequest
from mesa_client.client import AsyncMesaClient

# Create an MCP server instance
app = Server("mesa-mcp")

# Environment variables for MESA configuration
MESA_BASE_URL = os.getenv("MESA_BASE_URL", "http://localhost:8000/v3")
MESA_API_KEY = os.getenv("MESA_API_KEY")


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
                    "agent_id": {
                        "type": "string",
                        "description": "Tenant identifier for the AI agent (e.g., 'claude-desktop')",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Session or conversation identifier",
                    },
                    "content": {
                        "type": "string",
                        "description": "The actual information or memory to store",
                    },
                },
                "required": ["agent_id", "session_id", "content"],
            },
        ),
        types.Tool(
            name="search_memory",
            description="Search the MESA memory layer for relevant past information.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "Tenant identifier for the AI agent",
                    },
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
                "required": ["agent_id", "session_id", "query"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(
    name: str, arguments: dict[str, Any] | None
) -> list[types.TextContent]:
    """Handle execution of MESA tools."""
    if not arguments:
        raise ValueError("Missing tool arguments")

    agent_id = arguments.get("agent_id")
    session_id = arguments.get("session_id")

    if not agent_id or not session_id:
        return [
            types.TextContent(
                type="text", text="Error: agent_id and session_id are required."
            )
        ]

    try:
        async with AsyncMesaClient(
            base_url=MESA_BASE_URL, api_key=MESA_API_KEY
        ) as client:
            if name == "record_memory":
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

                if not resp.results:
                    return [
                        types.TextContent(
                            type="text", text="No relevant memories found."
                        )
                    ]

                formatted_results = []
                for i, res in enumerate(resp.results, 1):
                    formatted_results.append(
                        f"Result {i} (Score: {res.score:.4f}):\n{res.entity_name}"
                    )

                result_text = "\n\n".join(formatted_results)
                return [
                    types.TextContent(
                        type="text",
                        text=f"Found {resp.total} results:\n\n{result_text}",
                    )
                ]

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
