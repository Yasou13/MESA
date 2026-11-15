from __future__ import annotations

import os
import stat
from pathlib import Path

import httpx
import pytest

from mesa_mcp.antigravity_bridge import (
    EncryptedWriteSpool,
    GatewayClient,
    _credential_token,
    _drain_spool,
    _persistent_instance_id,
    _required_idempotency_key,
    create_mcp_server,
)
from mesa_mcp.antigravity_cli import _has_bridge_config, _install_config
from mesa_mcp.configuration import MCPSettings
from mesa_mcp.errors import MCPError
from mesa_mcp.workspace import workspace_fingerprint


def test_spool_and_persistent_instance_files_are_owner_only(tmp_path: Path):
    spool = EncryptedWriteSpool(tmp_path / "state")
    try:
        assert stat.S_IMODE((tmp_path / "state").stat().st_mode) == 0o700
        assert stat.S_IMODE((tmp_path / "state" / "antigravity-spool.key").stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / "state" / "antigravity-spool.db").stat().st_mode) == 0o600
        first = _persistent_instance_id(tmp_path / "state")
        assert first == _persistent_instance_id(tmp_path / "state")
        assert stat.S_IMODE((tmp_path / "state" / "antigravity-client-instance-id").stat().st_mode) == 0o600
    finally:
        spool.close()


def test_bridge_reads_only_private_xdg_credential_file(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    path = tmp_path / "config" / "mesa" / "antigravity" / "sha256:test.env"
    path.parent.mkdir(parents=True)
    path.write_text("MESA_ANTIGRAVITY_MCP_TOKEN='test-token'\n", encoding="utf-8")
    path.chmod(0o600)
    assert _credential_token("sha256:test") == "test-token"
    os.chmod(path, 0o644)
    with pytest.raises(ValueError, match="protected"):
        _credential_token("sha256:test")


def test_idempotency_is_required_and_http_auth_errors_are_not_retryable():
    assert _required_idempotency_key({"idempotency_key": "idem-1"}) == "idem-1"
    with pytest.raises(MCPError, match="idempotency_key is required"):
        _required_idempotency_key({})
    request = httpx.Request("POST", "http://gateway/mcp/v1/tools/call")
    with pytest.raises(MCPError) as auth_error:
        GatewayClient._raise_for_response(httpx.Response(401, request=request))
    assert not auth_error.value.retryable
    with pytest.raises(MCPError) as server_error:
        GatewayClient._raise_for_response(httpx.Response(503, request=request))
    assert server_error.value.retryable


def test_installed_antigravity_config_contains_no_token(tmp_path: Path):
    _install_config(tmp_path)
    config = (tmp_path / ".agents" / "mcp_config.json").read_text(encoding="utf-8")
    assert _has_bridge_config(tmp_path)
    assert "MESA_ANTIGRAVITY_MCP_TOKEN" not in config
    assert '"command": "mesa"' in config


@pytest.mark.asyncio
async def test_drain_spool_marks_non_retryable_error_failed_and_stops_resending(
    tmp_path: Path,
) -> None:
    spool = EncryptedWriteSpool(tmp_path / "spool")
    try:
        op_id = spool.enqueue(
            "mesa_remember", "idem-perm-fail", {"content": "hello"}
        )

        class PermanentFailGateway:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict]] = []

            async def call(self, tool_name: str, arguments: dict) -> dict:
                self.calls.append((tool_name, dict(arguments)))
                raise MCPError("INVALID_ARGUMENT", "bad payload", retryable=False)

        gateway = PermanentFailGateway()
        await _drain_spool(spool, gateway)  # type: ignore[arg-type]
        assert len(gateway.calls) == 1
        assert spool.pending() == []
        row = spool._db.execute(
            "SELECT status, last_error FROM outbound_operations WHERE operation_id = ?",
            (op_id,),
        ).fetchone()
        assert row == ("FAILED", "bad payload")

        await _drain_spool(spool, gateway)  # type: ignore[arg-type]
        assert len(gateway.calls) == 1
    finally:
        spool.close()


def test_create_mcp_server_isolates_spool_per_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    state_home = tmp_path / "state"
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    ws_a = tmp_path / "ws-a"
    ws_b = tmp_path / "ws-b"
    ws_a.mkdir()
    ws_b.mkdir()

    for ws in (ws_a, ws_b):
        fp = workspace_fingerprint(ws)
        cred = config_home / "mesa" / "antigravity" / f"{fp}.env"
        cred.parent.mkdir(parents=True, exist_ok=True)
        cred.write_text("MESA_ANTIGRAVITY_MCP_TOKEN='tok'\n", encoding="utf-8")
        cred.chmod(0o600)

    _app_a, _gw_a, spool_a = create_mcp_server(
        MCPSettings(workspace_root=ws_a, gateway_url="http://127.0.0.1:8001")
    )
    _app_b, _gw_b, spool_b = create_mcp_server(
        MCPSettings(workspace_root=ws_b, gateway_url="http://127.0.0.1:8001")
    )
    try:
        assert spool_a._key_path != spool_b._key_path
        assert spool_a._key_path.parent != spool_b._key_path.parent
        spool_a.enqueue("mesa_remember", "idem-a", {"content": "only in a"})
        assert len(spool_a.pending()) == 1
        assert spool_b.pending() == []
    finally:
        spool_a.close()
        spool_b.close()

