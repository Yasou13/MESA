"""Static reachability contract for the deprecated MCP HTTP router."""

from pathlib import Path

from cryptography.fernet import Fernet

from mesa_mcp.configuration import MCPSettings
from mesa_mcp.gateway.app import create_gateway_app

ROOT = Path(__file__).parents[1]


def test_production_gateway_does_not_register_legacy_heartbeat_router(
    tmp_path: Path,
) -> None:
    app = create_gateway_app(
        MCPSettings(
            MESA_GATEWAY_ENCRYPTION_KEY=Fernet.generate_key().decode(),
            MESA_GATEWAY_CONTROL_DB=tmp_path / "gateway.sqlite",
        )
    )
    paths = {route.path for route in app.routes}

    assert "/mcp/v1/heartbeat" not in paths
    assert "/mcp/v1/connect" not in paths
    assert "/mcp/v1/handshake" in paths


def test_production_entrypoint_has_no_legacy_router_import() -> None:
    app_source = (ROOT / "mesa_mcp" / "gateway" / "app.py").read_text(encoding="utf-8")
    legacy_source = (ROOT / "mesa_mcp" / "gateway" / "http_gateway.py").read_text(
        encoding="utf-8"
    )

    assert "http_gateway" not in app_source
    assert "deprecated and not production" in legacy_source
