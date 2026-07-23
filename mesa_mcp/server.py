import asyncio
import os
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from mesa_client.client import AsyncMesaClient, AsyncMesaV4Client

# Create an MCP server instance
app: Server = Server("mesa-mcp")

# Environment variables for MESA configuration
MESA_BASE_URL = os.getenv("MESA_BASE_URL", "http://localhost:8000")
MESA_API_KEY = os.getenv("MESA_API_KEY")
MESA_AGENT_ID = os.getenv("MESA_AGENT_ID")
MESA_TENANT_ID = os.getenv("MESA_TENANT_ID")
MESA_WORKSPACE_ID = os.getenv("MESA_WORKSPACE_ID")
MESA_DATASET_IDS = [
    item.strip()
    for item in os.getenv("MESA_DATASET_IDS", "").split(",")
    if item.strip()
]


@app.list_tools()  # type: ignore[untyped-decorator]
async def list_tools() -> list[types.Tool]:
    """Expose MESA memory functions as MCP tools."""
    return [
        types.Tool(
            name="catalog",
            description=(
                "Create or list authorized V4 workspaces, datasets, documents "
                "and immutable revisions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "list"]},
                    "resource": {
                        "type": "string",
                        "enum": ["workspace", "dataset", "document", "revision"],
                    },
                    "workspace_id": {"type": "string"},
                    "dataset_id": {"type": "string"},
                    "document_id": {"type": "string"},
                    "revision_id": {"type": "string"},
                    "name": {"type": "string"},
                    "title": {"type": "string"},
                    "external_ref": {"type": "string"},
                    "revision_number": {"type": "integer", "minimum": 1},
                    "content_sha256": {
                        "type": "string",
                        "pattern": "^[0-9a-fA-F]{64}$",
                    },
                    "supersedes_revision_id": {"type": "string"},
                },
                "required": ["action", "resource"],
            },
        ),
        types.Tool(
            name="start_session",
            description="Create a server-authorized V4 memory session for this agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
            },
        ),
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
                    "dataset_id": {"type": "string"},
                    "document_id": {"type": "string"},
                    "revision_id": {"type": "string"},
                    "chunk_id": {"type": "string"},
                    "title": {"type": "string"},
                    "source_ref": {"type": "string"},
                    "evidence_span": {"type": "string"},
                },
                "required": [
                    "session_id",
                    "dataset_id",
                    "document_id",
                    "revision_id",
                    "chunk_id",
                    "title",
                    "source_ref",
                    "content",
                ],
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
                    "dataset_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "jurisdiction": {"type": "string"},
                    "valid_at": {"type": "string"},
                },
                "required": ["session_id", "query"],
            },
        ),
        types.Tool(
            name="forget_memory",
            description="Purge one authorized V4 document without deleting shared entities.",
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "document_id": {"type": "string"},
                },
                "required": ["dataset_id", "document_id"],
            },
        ),
        types.Tool(
            name="get_stats",
            description="Return authenticated MESA API health without opening local storage.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="mutation_status",
            description="Return the durable V4 processing status for one mutation.",
            inputSchema={
                "type": "object",
                "properties": {"mutation_id": {"type": "string"}},
                "required": ["mutation_id"],
            },
        ),
        types.Tool(
            name="get_context",
            description="Return authorized V4 session context and mutation provenance.",
            inputSchema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="end_session",
            description="Request durable V4 finalization for an authorized session.",
            inputSchema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="rollback_mutation",
            description="Rollback only artifacts exclusively owned by one V4 mutation.",
            inputSchema={
                "type": "object",
                "properties": {"mutation_id": {"type": "string"}},
                "required": ["mutation_id"],
            },
        ),
        types.Tool(
            name="replay_mutation",
            description="Replay a V4 mutation in DLQ/BLOCKED state.",
            inputSchema={
                "type": "object",
                "properties": {"mutation_id": {"type": "string"}},
                "required": ["mutation_id"],
            },
        ),
    ]


@app.call_tool()  # type: ignore[untyped-decorator]
async def call_tool(
    name: str, arguments: dict[str, Any] | None
) -> list[types.TextContent]:
    """Handle execution of MESA tools."""
    if arguments is None:
        raise ValueError("Missing tool arguments")

    agent_id = MESA_AGENT_ID
    if not agent_id or not MESA_TENANT_ID or not MESA_WORKSPACE_ID:
        return [
            types.TextContent(
                type="text",
                text=(
                    "Error: MESA_AGENT_ID, MESA_TENANT_ID and "
                    "MESA_WORKSPACE_ID must be configured."
                ),
            )
        ]

    session_id = arguments.get("session_id")

    try:
        async with AsyncMesaClient(
            base_url=MESA_BASE_URL, api_key=MESA_API_KEY
        ) as client:
            if name == "catalog":
                action = str(arguments.get("action") or "")
                resource = str(arguments.get("resource") or "")
                workspace_id = str(
                    arguments.get("workspace_id") or MESA_WORKSPACE_ID
                )
                dataset_id = str(arguments.get("dataset_id") or "")
                document_id = str(arguments.get("document_id") or "")
                async with AsyncMesaV4Client(
                    base_url=MESA_BASE_URL, api_key=MESA_API_KEY
                ) as v4_client:
                    if (action, resource) == ("create", "workspace"):
                        response = await v4_client.create_workspace(
                            tenant_id=MESA_TENANT_ID,
                            workspace_id=workspace_id,
                            workspace_name=arguments.get("name"),
                        )
                    elif (action, resource) == ("list", "workspace"):
                        response = await v4_client.list_workspaces(
                            tenant_id=MESA_TENANT_ID
                        )
                    elif (action, resource) == ("create", "dataset"):
                        if not dataset_id:
                            raise ValueError("dataset_id is required")
                        response = await v4_client.create_dataset(
                            tenant_id=MESA_TENANT_ID,
                            workspace_id=workspace_id,
                            dataset_id=dataset_id,
                            dataset_name=arguments.get("name"),
                        )
                    elif (action, resource) == ("list", "dataset"):
                        response = await v4_client.list_datasets(
                            tenant_id=MESA_TENANT_ID,
                            workspace_id=workspace_id,
                        )
                    elif (action, resource) == ("create", "document"):
                        if not dataset_id or not document_id:
                            raise ValueError(
                                "dataset_id and document_id are required"
                            )
                        response = await v4_client.create_document(
                            tenant_id=MESA_TENANT_ID,
                            workspace_id=workspace_id,
                            dataset_id=dataset_id,
                            document_id=document_id,
                            title=str(arguments.get("title") or document_id),
                            external_ref=arguments.get("external_ref"),
                        )
                    elif (action, resource) == ("list", "document"):
                        if not dataset_id:
                            raise ValueError("dataset_id is required")
                        response = await v4_client.list_documents(
                            tenant_id=MESA_TENANT_ID,
                            workspace_id=workspace_id,
                            dataset_id=dataset_id,
                        )
                    elif (action, resource) == ("create", "revision"):
                        revision_id = str(arguments.get("revision_id") or "")
                        content_sha256 = str(
                            arguments.get("content_sha256") or ""
                        )
                        if not dataset_id or not document_id or not revision_id:
                            raise ValueError(
                                "dataset_id, document_id and revision_id are required"
                            )
                        response = await v4_client.create_revision(
                            tenant_id=MESA_TENANT_ID,
                            workspace_id=workspace_id,
                            dataset_id=dataset_id,
                            document_id=document_id,
                            revision_id=revision_id,
                            revision_number=int(
                                arguments.get("revision_number") or 1
                            ),
                            content_sha256=content_sha256,
                            supersedes_revision_id=arguments.get(
                                "supersedes_revision_id"
                            ),
                        )
                    elif (action, resource) == ("list", "revision"):
                        if not dataset_id or not document_id:
                            raise ValueError(
                                "dataset_id and document_id are required"
                            )
                        response = await v4_client.list_revisions(
                            tenant_id=MESA_TENANT_ID,
                            workspace_id=workspace_id,
                            dataset_id=dataset_id,
                            document_id=document_id,
                        )
                    else:
                        raise ValueError("unsupported catalog action/resource")
                return [types.TextContent(type="text", text=str(response))]

            if name == "start_session":
                dataset_ids = arguments.get("dataset_ids") or MESA_DATASET_IDS
                if not dataset_ids:
                    return [
                        types.TextContent(
                            type="text",
                            text="Error: at least one dataset_id is required.",
                        )
                    ]
                async with AsyncMesaV4Client(
                    base_url=MESA_BASE_URL, api_key=MESA_API_KEY
                ) as v4_client:
                    response = await v4_client.start_session(
                        tenant_id=MESA_TENANT_ID,
                        workspace_id=MESA_WORKSPACE_ID,
                        dataset_ids=dataset_ids,
                        agent_id=agent_id,
                    )
                return [types.TextContent(type="text", text=str(response))]

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

                async with AsyncMesaV4Client(
                    base_url=MESA_BASE_URL, api_key=MESA_API_KEY
                ) as v4_client:
                    response = await v4_client.insert(
                        session_id=session_id,
                        dataset_id=str(arguments.get("dataset_id") or ""),
                        document_id=str(arguments.get("document_id") or ""),
                        revision_id=str(arguments.get("revision_id") or ""),
                        chunk_id=str(arguments.get("chunk_id") or ""),
                        title=str(arguments.get("title") or ""),
                        source_ref=str(arguments.get("source_ref") or ""),
                        evidence_span=str(arguments.get("evidence_span") or ""),
                        content=content,
                    )

                return [
                    types.TextContent(
                        type="text",
                        text=(
                            "Memory accepted for V4 processing. "
                            f"Mutation ID: {response['mutation_id']}"
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

                async with AsyncMesaV4Client(
                    base_url=MESA_BASE_URL, api_key=MESA_API_KEY
                ) as v4_client:
                    resp = await v4_client.search(
                        session_id=session_id,
                        query=query,
                        dataset_ids=arguments.get("dataset_ids"),
                        limit=limit,
                        jurisdiction=arguments.get("jurisdiction"),
                        valid_at=arguments.get("valid_at"),
                    )
                results = resp.get("results", [])
                if not results:
                    return [
                        types.TextContent(
                            type="text", text="No relevant memories found."
                        )
                    ]

                formatted_results = []
                for i, res in enumerate(results, 1):
                    entity = res.get("entity", {})
                    content = entity.get("canonical_name", "")
                    formatted_results.append(
                        f"Result {i} (Score: {res.get('final_score', 0):.4f}):\n"
                        f"{content}\nProvenance: {res.get('provenance', [])}"
                    )

                result_text = "\n\n".join(formatted_results)
                return [
                    types.TextContent(
                        type="text",
                        text=f"Found {len(results)} results:\n\n{result_text}",
                    )
                ]

            elif name == "forget_memory":
                dataset_id = arguments.get("dataset_id")
                document_id = arguments.get("document_id")
                if not dataset_id or not document_id:
                    return [
                        types.TextContent(
                            type="text",
                            text="Error: dataset_id and document_id are required.",
                        )
                    ]
                async with AsyncMesaV4Client(
                    base_url=MESA_BASE_URL, api_key=MESA_API_KEY
                ) as v4_client:
                    purge_resp = await v4_client.purge_document(
                        tenant_id=MESA_TENANT_ID,
                        workspace_id=MESA_WORKSPACE_ID,
                        dataset_id=dataset_id,
                        document_id=document_id,
                    )
                return [
                    types.TextContent(
                        type="text",
                        text=f"Document purge accepted: {purge_resp}",
                    )
                ]

            elif name == "get_stats":
                health = await client._request("GET", "/v3/health")
                return [types.TextContent(type="text", text=f"Health: {health}")]

            elif name == "mutation_status":
                mutation_id = arguments.get("mutation_id")
                if not mutation_id:
                    return [types.TextContent(type="text", text="Error: mutation_id is required.")]
                async with AsyncMesaV4Client(
                    base_url=MESA_BASE_URL, api_key=MESA_API_KEY
                ) as v4_client:
                    response = await v4_client.status(mutation_id)
                return [types.TextContent(type="text", text=str(response))]

            elif name == "get_context":
                if not session_id:
                    return [types.TextContent(type="text", text="Error: session_id is required.")]
                async with AsyncMesaV4Client(
                    base_url=MESA_BASE_URL, api_key=MESA_API_KEY
                ) as v4_client:
                    response = await v4_client.get_context(
                        session_id=session_id
                    )
                return [types.TextContent(type="text", text=str(response))]

            elif name == "end_session":
                if not session_id:
                    return [types.TextContent(type="text", text="Error: session_id is required.")]
                async with AsyncMesaV4Client(
                    base_url=MESA_BASE_URL, api_key=MESA_API_KEY
                ) as v4_client:
                    response = await v4_client.end_session(
                        session_id=session_id
                    )
                return [types.TextContent(type="text", text=str(response))]

            elif name in {"rollback_mutation", "replay_mutation"}:
                mutation_id = arguments.get("mutation_id")
                if not mutation_id:
                    return [
                        types.TextContent(
                            type="text", text="Error: mutation_id is required."
                        )
                    ]
                async with AsyncMesaV4Client(
                    base_url=MESA_BASE_URL, api_key=MESA_API_KEY
                ) as v4_client:
                    response = (
                        await v4_client.rollback(mutation_id)
                        if name == "rollback_mutation"
                        else await v4_client.replay(mutation_id)
                    )
                return [types.TextContent(type="text", text=str(response))]

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
