from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from mesa_mcp import codex_cli


def test_env_file_is_private_and_round_trips_without_printing_secret(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    path = codex_cli._env_path("sha256:workspace")
    codex_cli._write_env(
        path, "token-not-for-output", "cred_123", "http://127.0.0.1:8765"
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    values = codex_cli._read_env(path)
    assert values["MESA_CODEX_CREDENTIAL_ID"] == "cred_123"
    assert values["MESA_CODEX_MCP_TOKEN"] == "token-not-for-output"


def test_project_install_merges_only_mesa_entries_and_takes_backups(tmp_path: Path):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        '[mcp_servers.other]\nurl = "http://other"\n\n[mcp_servers.mesa]\nurl = "http://old"\n',
        encoding="utf-8",
    )
    hooks = tmp_path / ".codex" / "hooks.json"
    hooks.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"command": "keep-me"}]},
                        {
                            "hooks": [
                                {
                                    "command": "python3 .codex/hooks/mesa_session_start.py"
                                }
                            ]
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    codex_cli._install_project_files(tmp_path, "http://127.0.0.1:8765")
    installed = config.read_text(encoding="utf-8")
    assert "[mcp_servers.other]" in installed
    assert 'url = "http://other"' in installed
    assert codex_cli._MANAGED_START in installed
    document = json.loads(hooks.read_text(encoding="utf-8"))
    assert "keep-me" in json.dumps(document)
    assert "mesa codex hook start" in json.dumps(document)
    assert "mesa_session_start.py" not in json.dumps(document)
    assert list(config.parent.glob("config.toml.mesa-backup-*"))
    assert list(hooks.parent.glob("hooks.json.mesa-backup-*"))
    codex_cli._uninstall_project_files(tmp_path)
    assert "[mcp_servers.other]" in config.read_text(encoding="utf-8")
    assert codex_cli._MANAGED_START not in config.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_rotation_verification_uses_streamable_mcp(monkeypatch):
    called: dict[str, str] = {}

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def initialize(self):
            called["initialized"] = "yes"

    class _Transport:
        async def __aenter__(self):
            return (None, None, None)

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(
        codex_cli, "streamable_http_client", lambda *args, **kwargs: _Transport()
    )
    monkeypatch.setattr(codex_cli, "ClientSession", lambda *args: _Session())
    await codex_cli._verify_gateway_credential("http://gateway", "secret")
    assert called == {"initialized": "yes"}
