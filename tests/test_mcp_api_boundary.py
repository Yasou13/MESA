from pathlib import Path

import pytest

pytestmark = pytest.mark.optional_mcp


def test_mcp_default_base_url_has_no_version_suffix():
    from mesa_mcp import server

    assert server.MESA_BASE_URL == "http://localhost:8000"


def test_mcp_stats_never_opens_storage_directly():
    from mesa_mcp import server

    source = Path(server.__file__).read_text(encoding="utf-8")
    assert "mesa_storage.dao" not in source
    assert "AsyncEngine" not in source
    assert 'client._request("GET", "/v3/health")' in source
