from __future__ import annotations

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI

from mesa_api.routers.control.router import create_control_router
from mesa_mcp.gateway.middleware import ControlPlaneMiddleware
from mesa_memory.security.rbac import AccessControl
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_codex_dashboard_summary_is_secret_safe_and_can_revoke(tmp_path):
    database = tmp_path / "control.sqlite"
    config = Config("mesa_storage/alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database}")
    command.upgrade(config, "head")
    engine = AsyncEngine(str(database))
    await engine.initialize()
    access_control = AccessControl(policy_path=str(tmp_path / "rbac.sqlite"))
    await access_control.initialize()
    await access_control.grant_control_role("control-admin")
    control = ControlPlaneMiddleware(engine=engine)
    await control.initialize()
    try:
        await control.client_repo.create_client("codex", "Codex", "codex", "principal")
        binding = await control.client_repo.add_project_binding(
            "codex", "sha256:workspace", "tenant", "workspace", "dataset"
        )
        record, token = await control.credential_repo.issue("codex", binding)
        await control.codex_profile_repo.ensure(binding)
        app = FastAPI()

        @app.middleware("http")
        async def attach_control_admin(request, call_next):
            request.state.principal = type(
                "Principal",
                (),
                {
                    "principal_id": "control-admin",
                    "principal_type": "USER",
                    "status": "active",
                },
            )()
            return await call_next(request)

        app.include_router(
            create_control_router(
                lambda: control.client_repo,
                lambda: control.conn_repo,
                lambda: control.settings_repo,
                lambda: control.policy_repo,
                lambda: control.activity_repo,
                lambda: control.approval_repo,
                lambda: control.credential_repo,
                lambda: control.codex_profile_repo,
                lambda: access_control,
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            summary = await client.get("/control/mcp/codex")
            assert summary.status_code == 200
            assert token not in summary.text
            assert "token_hash" not in summary.text
            assert record["credential_id"] in summary.text
            revoked = await client.post(
                f"/control/mcp/codex/credentials/{record['credential_id']}/revoke"
            )
        assert revoked.status_code == 200
        assert (await control.credential_repo.get_summary(record["credential_id"]))[
            "status"
        ] == "REVOKED"
    finally:
        await control.close()
        await access_control.close()


@pytest.mark.asyncio
async def test_control_routes_require_an_explicit_server_side_admin_role(tmp_path):
    database = tmp_path / "control.sqlite"
    config = Config("mesa_storage/alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database}")
    command.upgrade(config, "head")
    engine = AsyncEngine(str(database))
    await engine.initialize()
    control = ControlPlaneMiddleware(engine=engine)
    await control.initialize()
    access_control = AccessControl(policy_path=str(tmp_path / "rbac.sqlite"))
    await access_control.initialize()
    try:
        app = FastAPI()

        @app.middleware("http")
        async def attach_non_admin(request, call_next):
            request.state.principal = type(
                "Principal",
                (),
                {
                    "principal_id": "member",
                    "principal_type": "USER",
                    "status": "active",
                },
            )()
            return await call_next(request)

        app.include_router(
            create_control_router(
                lambda: control.client_repo,
                lambda: control.conn_repo,
                lambda: control.settings_repo,
                lambda: control.policy_repo,
                lambda: control.activity_repo,
                lambda: control.approval_repo,
                get_access_control=lambda: access_control,
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            forbidden = await client.get("/control/mcp/clients")
            assert forbidden.status_code == 403

        await access_control.grant_control_role("member")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            allowed = await client.get("/control/mcp/clients")
            assert allowed.status_code == 200
    finally:
        await control.close()
        await access_control.close()
