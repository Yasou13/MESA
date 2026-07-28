from mesa_memory.api.server import app


def test_api_wires_managed_mcp_control_routes() -> None:
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    for route in app.routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            paths.update(child.path for child in included.routes if hasattr(child, "path"))
    assert "/control/mcp/approvals/{approval_id}/decide" in paths
    assert "/control/mcp/credentials/{credential_id}/revoke" in paths
