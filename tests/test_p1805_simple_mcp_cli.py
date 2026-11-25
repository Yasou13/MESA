"""P18-05 tests: unified mesa CLI, mesa mcp config <client>, stdio mesa-mcp, and gateway (D97)."""

from __future__ import annotations

import json
import sys
import tomllib
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mesa_mcp import cli
from mesa_mcp.configuration import MCPSettings
from mesa_mcp.gateway.app import create_gateway_app
from mesa_mcp.local_cli import MCP_CONFIG_CLIENTS, handle_local_command


def test_mesa_help_lists_only_kept_commands() -> None:
    parser = cli._parser()
    help_text = parser.format_help()
    subparsers_action = next(
        action for action in parser._actions if hasattr(action, "choices") and action.choices
    )
    choices = set(subparsers_action.choices.keys())
    assert choices == {
        "start",
        "add",
        "search",
        "ask",
        "delete",
        "status",
        "mcp-config",
        "mcp",
        "operations",
    }
    assert "codex" not in choices
    assert "antigravity" not in choices
    assert "codex" not in help_text
    assert "antigravity" not in help_text


@pytest.mark.parametrize("client_name", MCP_CONFIG_CLIENTS)
def test_mesa_mcp_config_prints_valid_snippet_without_writing_files(
    client_name: str, tmp_path: Path, monkeypatch, capsys
) -> None:
    mesa_home = tmp_path / "mesa_home"
    mesa_home.mkdir()
    creds_path = mesa_home / "credentials.json"
    creds_path.write_text(
        json.dumps(
            {
                "base_url": "http://127.0.0.1:8123",
                "api_key": "test-local-key",
                "tenant_id": "local",
                "workspace_id": "default",
                "dataset_id": "default",
                "agent_id": "local-agent",
            }
        ),
        encoding="utf-8",
    )
    workdir = tmp_path / "empty_cwd"
    workdir.mkdir()
    monkeypatch.setenv("MESA_HOME", str(mesa_home))
    monkeypatch.chdir(workdir)

    args = cli._parser().parse_args(["mcp", "config", client_name])
    handle_local_command(args)

    captured = capsys.readouterr()
    assert "plaintext" in captured.err.lower()
    # Must never write any file into cwd or extra files into MESA_HOME.
    assert list(workdir.iterdir()) == []
    assert [p.name for p in mesa_home.iterdir()] == ["credentials.json"]

    if client_name == "codex":
        parsed = tomllib.loads(captured.out)
        server = parsed["mcp_servers"]["mesa"]
    else:
        parsed = json.loads(captured.out)
        server = parsed["mcpServers"]["mesa"]
        if client_name == "claude-code":
            assert server["type"] == "stdio"

    assert Path(server["command"]).name == "mesa-mcp"
    assert server["args"] == []
    assert server["env"]["MESA_BASE_URL"] == "http://127.0.0.1:8123"
    assert server["env"]["MESA_API_KEY"] == "test-local-key"
    assert server["env"]["MESA_TENANT_ID"] == "local"
    assert server["env"]["MESA_WORKSPACE_ID"] == "default"
    assert server["env"]["MESA_DATASET_ID"] == "default"
    assert server["env"]["MESA_ACTOR_ID"] == "local-agent"


@pytest.mark.asyncio
async def test_mesa_mcp_stdio_starts_and_lists_tools(tmp_path: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-B", "-m", "mesa_mcp.server"],
        cwd=tmp_path,
        env={
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "MESA_BASE_URL": "http://127.0.0.1:8000",
            "MESA_API_KEY": "stdio-check-key",
            "MESA_TRANSPORT": "stdio",
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=15)
        ) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            assert tool_names == {
                "mesa_health",
                "mesa_get_context",
                "mesa_remember",
                "mesa_recall",
                "mesa_ask",
                "mesa_improve",
                "mesa_forget",
            }


@pytest.mark.asyncio
async def test_mcp_gateway_still_starts_and_serves_health(tmp_path: Path) -> None:
    settings = MCPSettings(
        base_url="http://127.0.0.1:8000",
        api_key="gw-key",
        gateway_control_db=tmp_path / "control.db",
        gateway_encryption_key=Fernet.generate_key().decode("ascii"),
        workspace_root=tmp_path,
    )
    app = create_gateway_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://gateway",
        ) as client:
            resp = await client.get("/mcp/v1/health")
            assert resp.status_code == 401
