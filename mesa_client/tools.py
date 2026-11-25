"""OpenAI-format tool definitions and execution helpers for MESA (D104)."""

from __future__ import annotations

import json
from typing import Any

from mesa_client.client import (
    AsyncMesaV4Client,
    Mesa,
    MesaV4Client,
    MesaValidationError,
)

MESA_OPENAI_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": (
                "Search MESA documents and return ranked evidence passages with "
                "document IDs, revision numbers, character spans, validity dates, "
                "and PDF page / XLSX row locations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query text.",
                    },
                    "document_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 64,
                        "description": "Optional list of caller document IDs to restrict search.",
                    },
                    "valid_at": {
                        "type": "string",
                        "description": (
                            "Optional ISO-8601 timestamp with timezone offset "
                            "(e.g. '2024-01-01T00:00:00Z') for point-in-time search."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 8,
                        "description": "Maximum number of passages to return.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_text",
            "description": (
                "Read the full rebuilt text or an exact character slice of a "
                "document revision from MESA."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "string",
                        "description": "Document identifier to read.",
                    },
                    "revision_id": {
                        "type": "string",
                        "description": "Optional revision ID (defaults to ACTIVE revision).",
                    },
                    "start": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Optional inclusive start character offset.",
                    },
                    "end": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Optional exclusive end character offset.",
                    },
                },
                "required": ["document_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": "List documents available in the configured MESA dataset.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
]


def get_openai_tools() -> list[dict[str, Any]]:
    """Return a fresh copy of MESA's OpenAI-format tool definitions (D104)."""
    return json.loads(json.dumps(MESA_OPENAI_TOOLS))


def _unpack_tool_call(tool_call: Any) -> tuple[str, dict[str, Any]]:
    """Extract (function_name, arguments_dict) from an OpenAI tool call object or dict."""
    raw_name: Any = None
    raw_args: Any = None

    if isinstance(tool_call, dict):
        if isinstance(tool_call.get("function"), dict):
            raw_name = tool_call["function"].get("name")
            raw_args = tool_call["function"].get("arguments")
        else:
            raw_name = tool_call.get("name")
            raw_args = tool_call.get("arguments")
    else:
        func = getattr(tool_call, "function", None)
        if func is not None:
            raw_name = getattr(func, "name", None)
            raw_args = getattr(func, "arguments", None)
        else:
            raw_name = getattr(tool_call, "name", None)
            raw_args = getattr(tool_call, "arguments", None)

    if not isinstance(raw_name, str) or not raw_name.strip():
        raise MesaValidationError("tool_call must specify a non-empty function name")
    name = raw_name.strip()

    if raw_args is None or raw_args == "":
        args: dict[str, Any] = {}
    elif isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            raise MesaValidationError(
                f"tool_call arguments must be valid JSON: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise MesaValidationError("tool_call arguments must decode to a JSON object")
        args = parsed
    elif isinstance(raw_args, dict):
        args = dict(raw_args)
    else:
        raise MesaValidationError("tool_call arguments must be a JSON string or dict")

    return name, args


def execute_tool_call(
    client: Mesa | MesaV4Client,
    tool_call: Any,
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    dataset_id: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Execute one OpenAI tool call ('search', 'read_text', 'list_documents') against MESA."""
    name, args = _unpack_tool_call(tool_call)

    if isinstance(client, Mesa):
        resolved_tenant = tenant_id or client.tenant_id
        resolved_workspace = workspace_id or client.workspace_id
        resolved_dataset = dataset_id or client.dataset_id
        resolved_agent = agent_id or client.agent_id
        v4 = client._client
        client._ensure_scope()
    else:
        if not tenant_id or not workspace_id or not dataset_id:
            raise MesaValidationError(
                "tenant_id, workspace_id, and dataset_id are required with MesaV4Client"
            )
        resolved_tenant = tenant_id
        resolved_workspace = workspace_id
        resolved_dataset = dataset_id
        resolved_agent = agent_id
        v4 = client

    if name == "search":
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise MesaValidationError("search requires a non-empty string 'query'")
        return v4.search(
            tenant_id=resolved_tenant,
            workspace_id=resolved_workspace,
            agent_id=resolved_agent,
            dataset_ids=[resolved_dataset],
            query=query,
            document_ids=args.get("document_ids"),
            valid_at=args.get("valid_at"),
            limit=int(args.get("limit", 8)),
        )

    if name == "read_text":
        document_id = args.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            raise MesaValidationError("read_text requires a non-empty 'document_id'")
        return v4.get_document_text(
            tenant_id=resolved_tenant,
            workspace_id=resolved_workspace,
            dataset_id=resolved_dataset,
            document_id=document_id,
            revision_id=args.get("revision_id"),
            start=args.get("start"),
            end=args.get("end"),
        )

    if name == "list_documents":
        return v4.list_documents(
            tenant_id=resolved_tenant,
            workspace_id=resolved_workspace,
            dataset_id=resolved_dataset,
        )

    raise MesaValidationError(f"Unknown MESA tool: '{name}'")


async def aexecute_tool_call(
    client: AsyncMesaV4Client,
    tool_call: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    dataset_id: str,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Async counterpart of :func:`execute_tool_call` for :class:`AsyncMesaV4Client`."""
    name, args = _unpack_tool_call(tool_call)

    if name == "search":
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise MesaValidationError("search requires a non-empty string 'query'")
        return await client.search(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            query=query,
            document_ids=args.get("document_ids"),
            valid_at=args.get("valid_at"),
            limit=int(args.get("limit", 8)),
        )

    if name == "read_text":
        document_id = args.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            raise MesaValidationError("read_text requires a non-empty 'document_id'")
        return await client.get_document_text(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            document_id=document_id,
            revision_id=args.get("revision_id"),
            start=args.get("start"),
            end=args.get("end"),
        )

    if name == "list_documents":
        return await client.list_documents(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            dataset_id=dataset_id,
        )

    raise MesaValidationError(f"Unknown MESA tool: '{name}'")
