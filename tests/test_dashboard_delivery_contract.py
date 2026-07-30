"""Control Dashboard delivery and Showcase safety contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from mesa_memory.config import (
    RuntimeEnvironment,
    RuntimeProfile,
    RuntimeProfileConfig,
    RuntimeProfileError,
    load_runtime_profile,
)
from mesa_runtime.dashboard import install_dashboard
from mesa_runtime.demo import ensure_demo_mode


def _settings(
    tmp_path: Path,
    *,
    environment: RuntimeEnvironment = RuntimeEnvironment.PRODUCTION,
    demo: bool = False,
) -> RuntimeProfileConfig:
    return RuntimeProfileConfig(
        profile=RuntimeProfile.API_ONLY,
        storage_root=tmp_path / "storage",
        load_dotenv=False,
        dotenv_path=None,
        model_enabled=False,
        external_provider_enabled=False,
        api_enabled=True,
        worker_enabled=False,
        require_worker_readiness=False,
        environment=environment,
        showcase_demo_enabled=demo,
    )


@pytest.mark.asyncio
async def test_dashboard_serves_assets_and_secret_free_runtime_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    (dashboard / "index.html").write_text("<h1>MESA</h1>", encoding="utf-8")
    monkeypatch.setenv("MESA_DASHBOARD_STATIC_ROOT", str(dashboard))
    settings = _settings(
        tmp_path, environment=RuntimeEnvironment.DEVELOPMENT, demo=True
    )
    app = FastAPI()
    install_dashboard(app, settings_provider=lambda: settings)

    routes = {route.path: route.endpoint for route in app.routes}
    root_response = await routes["/dashboard/{full_path:path}"]("")
    spa_response = await routes["/dashboard/{full_path:path}"]("unknown-route")
    assert isinstance(root_response, FileResponse)
    assert Path(root_response.path).read_text(encoding="utf-8") == "<h1>MESA</h1>"
    assert Path(spa_response.path).read_text(encoding="utf-8") == "<h1>MESA</h1>"
    assert await routes["/dashboard/runtime-config.json"]() == {
        "showcase_demo_enabled": True
    }


def test_demo_route_is_hidden_when_operator_has_not_enabled_it(tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        ensure_demo_mode(_settings(tmp_path))
    assert exc.value.status_code == 404


def test_production_rejects_demo_and_unauthenticated_modes(tmp_path: Path) -> None:
    base = {
        "MESA_RUNTIME_PROFILE": "api-only",
        "MESA_STORAGE_ROOT": str(tmp_path / "storage"),
        "MESA_ENVIRONMENT": "production",
    }
    with pytest.raises(RuntimeProfileError, match="development-only"):
        load_runtime_profile({**base, "MESA_SHOWCASE_DEMO_ENABLED": "true"})
    with pytest.raises(RuntimeProfileError, match="development-only"):
        load_runtime_profile({**base, "MESA_ALLOW_UNAUTHENTICATED": "true"})


def test_storage_defaults_to_xdg_data_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    data_home = tmp_path / "xdg-data"
    settings = load_runtime_profile(
        {
            "MESA_RUNTIME_PROFILE": "api-only",
            "XDG_DATA_HOME": str(data_home),
        }
    )
    assert settings.storage_root == (data_home / "mesa").resolve()
