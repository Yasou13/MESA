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
    _persistent_instance_id,
    _required_idempotency_key,
)
from mesa_mcp.antigravity_cli import _has_bridge_config, _install_config
from mesa_mcp.errors import MCPError


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
