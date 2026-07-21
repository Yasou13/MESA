"""Regression coverage for auth configuration loaded after explicit dotenv setup."""

from mesa_memory.api import server


def test_refresh_auth_config_reads_current_environment(monkeypatch) -> None:
    previous = (
        server._MESA_API_KEY,
        server._MESA_PRINCIPAL_ID,
        server._MESA_PRINCIPAL_TYPE,
        server._MESA_PRINCIPAL_STATUS,
    )
    try:
        monkeypatch.setenv("MESA_API_KEY", "dotenv-test-key")
        monkeypatch.setenv("MESA_PRINCIPAL_ID", "dotenv-test-principal")
        monkeypatch.setenv("MESA_PRINCIPAL_TYPE", "USER")
        monkeypatch.setenv("MESA_PRINCIPAL_STATUS", "inactive")
        server._refresh_auth_config()
        assert server._MESA_API_KEY == "dotenv-test-key"
        assert server._MESA_PRINCIPAL_ID == "dotenv-test-principal"
        assert server._MESA_PRINCIPAL_TYPE == "USER"
        assert server._MESA_PRINCIPAL_STATUS == "inactive"
    finally:
        (
            server._MESA_API_KEY,
            server._MESA_PRINCIPAL_ID,
            server._MESA_PRINCIPAL_TYPE,
            server._MESA_PRINCIPAL_STATUS,
        ) = previous
