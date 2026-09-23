"""Production gateway reachability contract for removed legacy routes."""

from pathlib import Path

from cryptography.fernet import Fernet

from mesa_mcp.configuration import MCPSettings
from mesa_mcp.gateway.app import create_gateway_app


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
