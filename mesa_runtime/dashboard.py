"""Static Control Dashboard delivery for the canonical runtime."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from mesa_memory.config import RuntimeProfileConfig


def dashboard_static_root() -> Path:
    """Resolve an explicit dev build first, then immutable packaged assets."""
    configured = os.environ.get("MESA_DASHBOARD_STATIC_ROOT")
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    packaged = Path(__file__).parent / "static" / "dashboard"
    if packaged.is_dir():
        return packaged
    return Path(__file__).parents[1] / "apps" / "control-dashboard" / "dist"


def dashboard_static_file(root: Path, requested: str) -> Path | None:
    """Resolve a public file without allowing traversal or symlink escape."""
    if not requested:
        return None
    dashboard_root = root.resolve(strict=False)
    requested_path = (dashboard_root / unquote(requested)).resolve(strict=False)
    if not requested_path.is_relative_to(dashboard_root):
        return None
    if requested_path.is_dir():
        requested_path = (requested_path / "index.html").resolve(strict=False)
    if (
        not requested_path.is_relative_to(dashboard_root)
        or not requested_path.is_file()
    ):
        return None
    return requested_path


def install_dashboard(
    application: FastAPI,
    *,
    settings_provider: Callable[[], RuntimeProfileConfig | None],
) -> None:
    """Install public static routes and a secret-free runtime capability feed."""

    async def runtime_config() -> dict[str, bool]:
        settings = settings_provider()
        return {
            "showcase_demo_enabled": bool(
                settings is not None and settings.showcase_demo_enabled
            )
        }

    async def dashboard_redirect() -> RedirectResponse:
        return RedirectResponse("/dashboard/")

    async def serve_dashboard(full_path: str) -> FileResponse:
        root = dashboard_static_root()
        if not root.is_dir():
            raise HTTPException(status_code=503, detail="Dashboard assets unavailable")
        asset = dashboard_static_file(root, full_path)
        if asset is not None:
            return FileResponse(asset)
        index = dashboard_static_file(root, "index.html")
        if index is None:
            raise HTTPException(status_code=503, detail="Dashboard index unavailable")
        return FileResponse(index)

    application.add_api_route(
        "/dashboard/runtime-config.json",
        runtime_config,
        methods=["GET"],
        include_in_schema=False,
    )
    application.add_api_route(
        "/dashboard",
        dashboard_redirect,
        methods=["GET"],
        include_in_schema=False,
    )
    application.add_api_route(
        "/dashboard/{full_path:path}",
        serve_dashboard,
        methods=["GET"],
        include_in_schema=False,
    )
