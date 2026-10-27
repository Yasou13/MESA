from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from unittest.mock import MagicMock, patch

import pytest

from mesa_mcp import codex_cli
from mesa_mcp.local_cli import (
    add_command,
    delete_command,
    mcp_config_command,
    provision_credentials,
    search_command,
    start_command,
    status_command,
)
from mesa_memory.security.api_keys import APIKeyStore
from mesa_memory.security.rbac import AccessControl


@pytest.mark.asyncio
async def test_provision_credentials_lifecycle(tmp_path, capsys):
    home = tmp_path / "mesa_home"
    storage_root = tmp_path / "storage"
    port = 8899

    # 1. First run provisions new key and grants
    creds = await provision_credentials(home, storage_root, port=port)
    captured = capsys.readouterr()

    creds_path = home / "credentials.json"
    assert creds_path.is_file()

    # Mode must be 0600
    file_mode = stat.S_IMODE(os.stat(creds_path).st_mode)
    assert file_mode == 0o600

    # Key must NOT be printed
    assert creds["api_key"] not in captured.out
    assert creds["api_key"] not in captured.err
    assert "Credentials written to" in captured.out

    # Content check
    assert creds["base_url"] == f"http://127.0.0.1:{port}"
    assert creds["tenant_id"] == "local"
    assert creds["workspace_id"] == "default"
    assert creds["dataset_id"] == "default"
    assert creds["agent_id"] == "local-agent"

    # Verify key is active in APIKeyStore
    policy_path = storage_root / "rbac_policy.db"
    key_store = APIKeyStore(str(policy_path))
    await key_store.initialize()
    verified = await key_store.verify(creds["api_key"])
    assert verified is not None
    assert verified.status == "active"
    assert verified.principal_id == "local-admin"

    # Verify grants in AccessControl
    access = AccessControl(str(policy_path))
    await access.initialize()
    # Check tenant OWNER
    assert await access.check_scope_role(
        "local-admin",
        tenant_id="local",
        workspace_id="default",
        dataset_id="default",
        required_role="OWNER",
    )
    # Check agent SESSION_CREATE
    assert await access.check_principal_permission("local-admin", "local-agent", "SESSION_CREATE")
    # Check dataset PURGE and ROLLBACK
    assert await access.check_dataset_permission("local-admin", tenant_id="local", dataset_id="default", permission="PURGE")
    assert await access.check_dataset_permission("local-admin", tenant_id="local", dataset_id="default", permission="ROLLBACK")

    # 2. Second run reuses the existing active key
    creds2 = await provision_credentials(home, storage_root, port=port)
    captured2 = capsys.readouterr()
    assert creds2["api_key"] == creds["api_key"]
    assert "Using existing active credentials" in captured2.out
    assert creds["api_key"] not in captured2.out


def test_start_command_windows_check(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(SystemExit) as exc:
        start_command(argparse.Namespace())
    assert exc.value.code == 1
    out, _ = capsys.readouterr()
    assert "MESA runs on Linux and macOS; on Windows use Docker or WSL" in out


def test_mcp_config_output(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    creds_path = home / "credentials.json"
    creds_path.write_text(
        json.dumps({
            "base_url": "http://127.0.0.1:8123",
            "api_key": "sec-mcp-key",
            "tenant_id": "test-tenant",
            "workspace_id": "test-ws",
            "dataset_id": "test-ds",
            "agent_id": "test-actor",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("MESA_HOME", str(home))

    mcp_config_command(argparse.Namespace())
    captured = capsys.readouterr()

    # Warning on stderr
    assert "Warning: the configuration below contains your MESA API key in plaintext." in captured.err

    # Valid JSON on stdout
    config = json.loads(captured.out)
    server_entry = config["mcpServers"]["mesa"]
    assert "command" in server_entry
    env = server_entry["env"]
    assert env["MESA_BASE_URL"] == "http://127.0.0.1:8123"
    assert env["MESA_API_KEY"] == "sec-mcp-key"
    assert env["MESA_TENANT_ID"] == "test-tenant"
    assert env["MESA_WORKSPACE_ID"] == "test-ws"
    assert env["MESA_DATASET_ID"] == "test-ds"
    assert env["MESA_ACTOR_ID"] == "test-actor"


def test_status_command(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    creds_path = home / "credentials.json"
    creds_path.write_text(
        json.dumps({"base_url": "http://mock-server", "api_key": "key"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MESA_HOME", str(home))

    mock_client = MagicMock()
    mock_resp_init = MagicMock(status_code=200, json=lambda: {"status": "ready"})
    mock_resp_health = MagicMock(status_code=200, json=lambda: {"status": "healthy"})
    mock_client.get.side_effect = [mock_resp_init, mock_resp_health]

    with patch("httpx.Client") as mock_httpx:
        mock_httpx.return_value.__enter__.return_value = mock_client
        # Human-readable status
        status_command(argparse.Namespace(json=False))
        out, _ = capsys.readouterr()
        assert "MESA Server: http://mock-server" in out
        assert "Readiness (/health/init): ready" in out
        assert "Health (/health): healthy" in out

        # JSON status
        mock_client.get.side_effect = [mock_resp_init, mock_resp_health]
        status_command(argparse.Namespace(json=True))
        out_json, _ = capsys.readouterr()
        parsed = json.loads(out_json)
        assert parsed["init"]["status"] == "ready"
        assert parsed["health"]["status"] == "healthy"


def test_cli_add_search_delete(tmp_path, monkeypatch, capsys):
    # Mock Mesa client
    mock_mesa = MagicMock()
    mock_mesa.add.return_value = {
        "document_id": "doc-test-1",
        "revision_id": "rev-1",
        "chunk_count": 3,
        "duplicate": False,
    }
    mock_mesa.search.return_value = [
        {"memory_id": "m1", "score": 0.92, "content": "Evidence sentence."}
    ]
    mock_mesa.delete.return_value = {"status": "purged", "document_id": "doc-test-1"}

    with patch("mesa_client.Mesa", return_value=mock_mesa):
        # 1. add file
        test_file = tmp_path / "kira.txt"
        test_file.write_text("Kira sozlesmesi metni", encoding="utf-8")
        add_command(argparse.Namespace(file=str(test_file), source="file://kira.txt", title="Kira", json=False))
        out, _ = capsys.readouterr()
        assert "Added document doc-test-1 with 3 chunk(s)" in out

        # 2. search
        search_command(argparse.Namespace(query="kira", limit=5, json=False))
        out, _ = capsys.readouterr()
        assert "score: 0.920" in out
        assert "Evidence sentence." in out

        # 3. delete
        delete_command(argparse.Namespace(document_id="doc-test-1", json=False))
        out, _ = capsys.readouterr()
        assert "Purged document doc-test-1" in out


def test_codex_cli_parser_routes_to_local_commands():
    parser = codex_cli._parser()
    args_start = parser.parse_args(["start", "--port", "9000"])
    assert args_start.group == "start"
    assert args_start.port == 9000

    args_add = parser.parse_args(["add", "doc.txt", "--source", "file://doc.txt", "--json"])
    assert args_add.group == "add"
    assert args_add.file == "doc.txt"
    assert args_add.source == "file://doc.txt"
    assert args_add.json is True

    args_search = parser.parse_args(["search", "test query", "--limit", "3"])
    assert args_search.group == "search"
    assert args_search.query == "test query"
    assert args_search.limit == 3

    args_delete = parser.parse_args(["delete", "doc-123"])
    assert args_delete.group == "delete"
    assert args_delete.document_id == "doc-123"

    args_status = parser.parse_args(["status", "--json"])
    assert args_status.group == "status"
    assert args_status.json is True

    args_mcp = parser.parse_args(["mcp-config"])
    assert args_mcp.group == "mcp-config"


def test_real_server_answers_health_init(tmp_path, monkeypatch):
    import asyncio
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    from mesa_memory.api import server

    home = tmp_path / "home"
    storage = tmp_path / "storage"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    asyncio.run(provision_credentials(home, storage, port))

    monkeypatch.setenv("MESA_STORAGE_ROOT", str(storage))
    monkeypatch.setenv("MESA_RUNTIME_PROFILE", "combined")
    monkeypatch.setenv("MESA_MODEL_ENABLED", "false")
    monkeypatch.setenv("MESA_LOAD_DOTENV", "false")

    config = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)

    t = threading.Thread(target=srv.run, daemon=True)
    t.start()

    ready = False
    for _ in range(50):
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/health/init", timeout=1.0)
            if r.status_code == 200 and r.json().get("status") == "ready":
                ready = True
                break
        except Exception:
            time.sleep(0.1)

    try:
        assert ready is True
    finally:
        srv.should_exit = True
        t.join(timeout=3.0)
