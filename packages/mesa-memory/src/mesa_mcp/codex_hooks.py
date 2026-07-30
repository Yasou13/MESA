"""Fail-open lifecycle hooks for the repository-local Codex integration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .workspace import workspace_fingerprint

_UNAVAILABLE = "MESA memory is temporarily unavailable. Continue using repository context; do not assume prior decisions were loaded."


def main(event: str) -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, TypeError):
        return 0
    if event == "end":
        asyncio.run(_end(payload))
        return 0
    payload["hook_event_name"] = "SessionStart" if event == "start" else "PostCompact"
    context = asyncio.run(_context(payload))
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": (
                        "SessionStart" if event == "start" else "PostCompact"
                    ),
                    "additionalContext": context,
                }
            },
            ensure_ascii=False,
        )
    )
    return 0


async def _context(payload: dict[str, Any]) -> str:
    root = Path(str(payload.get("cwd") or Path.cwd())).resolve()
    session_id = str(payload.get("session_id") or "")
    cache = _cache_path(root)
    try:
        fingerprint = workspace_fingerprint(root)
        started = await _post(
            "/mcp/v1/codex/sessions/start",
            {"session_id": session_id, "workspace_fingerprint": fingerprint},
        )
        profile = started.get("profile") or {}
        event = str(payload.get("hook_event_name") or "SessionStart")
        if event == "SessionStart" and not profile.get("session_start_enabled", True):
            return ""
        if event == "PostCompact" and not profile.get("post_compact_enabled", True):
            return ""
        result = await _recall(profile)
        text = str(result.get("context_text") or "")[:9600]
        if text:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(text, encoding="utf-8")
            return "MESA project memory:\n" + text
    except Exception:
        if cache.exists():
            try:
                return (
                    "MESA project memory (cached):\n"
                    + cache.read_text(encoding="utf-8")[:9600]
                )
            except OSError:
                pass
    return _UNAVAILABLE


async def _end(payload: dict[str, Any]) -> None:
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return
    try:
        await _post("/mcp/v1/codex/sessions/end", {"session_id": session_id})
    except Exception:
        pass


async def _post(path: str, payload: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=2.0, headers=_headers()) as client:
        response = await client.post(_gateway_url() + path, json=payload)
        response.raise_for_status()
        decoded = response.json()
        return decoded if isinstance(decoded, dict) else {}


async def _recall(profile: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=6.0, headers=_headers()) as client:
        async with streamable_http_client(
            _gateway_url() + "/mcp", http_client=client
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.call_tool(
                    "mesa_recall",
                    {
                        "query": "architecture decisions constraints conventions",
                        "mode": "context",
                        "limit": min(max(int(profile.get("max_records", 8)), 1), 8),
                        "token_budget": min(
                            max(int(profile.get("max_tokens", 2500)), 1), 2500
                        ),
                        "memory_types": profile.get(
                            "memory_types",
                            ["architecture", "decision", "constraint", "convention"],
                        ),
                    },
                )
    if response.isError or not response.content:
        raise RuntimeError("MESA recall failed")
    return json.loads(response.content[0].text)


def _headers() -> dict[str, str]:
    token = os.environ.get("MESA_CODEX_MCP_TOKEN", "")
    if not token:
        raise RuntimeError("MESA_CODEX_MCP_TOKEN is unavailable")
    return {"Authorization": f"Bearer {token}"}


def _gateway_url() -> str:
    return os.environ.get("MESA_CODEX_GATEWAY_URL", "http://127.0.0.1:8765").rstrip("/")


def _cache_path(root: Path) -> Path:
    cache_root = (
        Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "mesa-codex"
    )
    return cache_root / (hashlib.sha256(str(root).encode()).hexdigest() + ".context")
