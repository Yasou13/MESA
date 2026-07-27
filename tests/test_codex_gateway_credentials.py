from __future__ import annotations

import json

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet

from mesa_mcp.gateway.auth import GatewayPrincipal
from mesa_mcp.gateway.middleware import ControlPlaneMiddleware
from mesa_mcp.gateway.operations import GatewayOperationService
from mesa_storage.sqlite_engine import AsyncEngine


class FakeV4:
    async def health(self):
        return {"status": "healthy"}

    async def v4_recall(self, **_kwargs):
        return [
            {
                "memory_id": "m1",
                "memory_type": "decision",
                "content": "Use V4.",
                "provenance": {"mutation_id": "mut1"},
            },
            {"memory_id": "m2", "memory_type": "fact", "content": "Ignore me."},
        ]

    async def v4_remember(self, **_kwargs):
        return {"mutation_id": "mut1"}

    async def v4_improve(self, **_kwargs):
        return {"mutation_id": "mut2"}

    async def v4_forget(self, **_kwargs):
        return {"mutation_id": "mut3"}


@pytest.fixture
def control_db(tmp_path):
    database = tmp_path / "control.sqlite"
    config = Config("mesa_storage/alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database}")
    command.upgrade(config, "head")
    return database


@pytest.mark.asyncio
async def test_credential_is_hashed_scoped_and_revocable(control_db):
    engine = AsyncEngine(str(control_db))
    await engine.initialize()
    middleware = ControlPlaneMiddleware(engine=engine)
    await middleware.initialize()
    try:
        await middleware.client_repo.create_client(
            "codex-a", "Codex", "codex", "principal-a"
        )
        binding = await middleware.client_repo.add_project_binding(
            "codex-a", "sha256:repo-a", "tenant-a", "workspace-a", "dataset-a"
        )
        record, token = await middleware.credential_repo.issue("codex-a", binding)
        assert token not in record.values()
        resolved = await middleware.credential_repo.authenticate(token)
        assert resolved is not None
        assert resolved["binding_id"] == binding
        assert await middleware.credential_repo.revoke(record["credential_id"])
        assert await middleware.credential_repo.authenticate(token) is None
    finally:
        await middleware.close()


@pytest.mark.asyncio
async def test_direct_principal_filters_recall_and_never_uses_caller_scope(control_db):
    engine = AsyncEngine(str(control_db))
    await engine.initialize()
    middleware = ControlPlaneMiddleware(engine=engine)
    await middleware.initialize()
    try:
        await middleware.client_repo.create_client(
            "codex-a", "Codex", "codex", "principal-a"
        )
        binding = await middleware.client_repo.add_project_binding(
            "codex-a", "sha256:repo-a", "tenant-a", "workspace-a", "dataset-a"
        )
        service = GatewayOperationService(
            engine=engine,
            middleware=middleware,
            v4_service=FakeV4(),
            encryption_key=Fernet.generate_key().decode(),
        )
        result = await service.call_tool_for_principal(
            principal=GatewayPrincipal("codex-a", "cred-a", binding),
            tool_name="mesa_recall",
            arguments={
                "query": "architecture",
                "mode": "context",
                "memory_types": ["decision"],
                "project_id": "attacker-controlled",
            },
        )
        assert result["context_text"] == "[decision:m1]\nUse V4."
        assert result["memories"][0]["provenance"]["mutation_id"] == "mut1"
    finally:
        await middleware.close()


@pytest.mark.asyncio
async def test_binding_profile_revision_invalidates_context_recall_cache(control_db):
    engine = AsyncEngine(str(control_db))
    await engine.initialize()
    middleware = ControlPlaneMiddleware(engine=engine)
    await middleware.initialize()
    try:
        await middleware.client_repo.create_client(
            "codex-a", "Codex", "codex", "principal-a"
        )
        binding = await middleware.client_repo.add_project_binding(
            "codex-a", "sha256:repo-a", "tenant-a", "workspace-a", "dataset-a"
        )
        service = GatewayOperationService(
            engine=engine,
            middleware=middleware,
            v4_service=FakeV4(),
            encryption_key=Fernet.generate_key().decode(),
        )
        principal = GatewayPrincipal("codex-a", "cred-a", binding)
        initial = await service.call_tool_for_principal(
            principal=principal,
            tool_name="mesa_recall",
            arguments={"query": "architecture", "mode": "context"},
        )
        assert initial["memories"][0]["memory_type"] == "decision"
        updated = await middleware.codex_profile_repo.update(
            binding, memory_types=["fact"]
        )
        assert updated["revision"] == 2
        changed = await service.call_tool_for_principal(
            principal=principal,
            tool_name="mesa_recall",
            arguments={"query": "architecture", "mode": "context"},
        )
        assert changed["cache_status"] == "MISS"
        assert changed["memories"][0]["memory_type"] == "fact"
    finally:
        await middleware.close()


def test_hook_is_fail_open_and_does_not_echo_environment_secret(
    tmp_path, monkeypatch, capsys
):
    from mesa_mcp import codex_hooks

    monkeypatch.setenv("MESA_CODEX_MCP_TOKEN", "secret-token-must-not-appear")
    monkeypatch.setattr(
        codex_hooks.sys,
        "stdin",
        __import__("io").StringIO(
            json.dumps({"cwd": str(tmp_path), "session_id": "s1"})
        ),
    )
    assert codex_hooks.main("start") == 0
    output = capsys.readouterr().out
    assert "secret-token-must-not-appear" not in output
    assert "hookSpecificOutput" in output
