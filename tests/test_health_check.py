"""Health CLI contracts without a live server, provider or database."""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def probe(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "mesa_health_probe", Path(__file__).parents[1] / "scripts/health_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    opener = MagicMock()
    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *_: opener)
    monkeypatch.setenv("MESA_BASE_URL", "https://mesa.example/prefix/")
    monkeypatch.setenv("MESA_API_KEY", "secret-canary")
    return module, opener


def response(body):
    result = io.BytesIO(body)
    result.status = 200
    return result


def ready():
    return response(b'{"status":"ready"}')


def test_checks_readiness_and_authenticated_health_at_configured_base(probe, capsys):
    module, opener = probe
    opener.open.side_effect = [ready(), response(b'{"status":"healthy"}')]
    assert module.main() == 0
    first, second = opener.open.call_args_list
    assert first.args[0].full_url == "https://mesa.example/prefix/health/init"
    assert first.args[0].get_header("X-api-key") is None
    assert second.args[0].full_url == "https://mesa.example/prefix/health"
    assert second.args[0].get_header("X-api-key") == "secret-canary"
    assert first.kwargs["timeout"] == second.kwargs["timeout"] == 5
    assert "secret-canary" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "body",
    [
        b'{"status":"degraded"}',
        b'{"status":"maintenance"}',
        b"{}",
        b"[]",
        b"not JSON secret-canary",
    ],
)
def test_http_200_does_not_hide_unhealthy_or_invalid_response(probe, capsys, body):
    module, opener = probe
    opener.open.side_effect = [ready(), response(body)]
    assert module.main() == 1
    output = capsys.readouterr().out
    assert "FAIL:" in output
    assert "secret-canary" not in output


def test_missing_key_fails_after_readiness_without_anonymous_health_call(
    probe, monkeypatch, capsys
):
    module, opener = probe
    monkeypatch.delenv("MESA_API_KEY")
    opener.open.return_value = ready()
    assert module.main() == 1
    assert opener.open.call_count == 1
    assert "MESA_API_KEY is required" in capsys.readouterr().out


@pytest.mark.parametrize("code", [401, 403, 503, 302])
def test_http_errors_fail_without_printing_server_secrets(probe, capsys, code):
    module, opener = probe
    opener.open.side_effect = [
        ready(),
        urllib.error.HTTPError(
            "https://mesa.example",
            code,
            "secret-canary",
            {},
            io.BytesIO(b"secret-canary"),
        ),
    ]
    assert module.main() == 1
    output = capsys.readouterr().out
    assert f"HTTP {code}" in output
    assert "secret-canary" not in output


@pytest.mark.parametrize(
    "error", [TimeoutError("secret-canary"), urllib.error.URLError("secret-canary")]
)
def test_unreachable_api_fails_without_attempting_health(probe, capsys, error):
    module, opener = probe
    opener.open.side_effect = error
    assert module.main() == 1
    assert opener.open.call_count == 1
    assert "secret-canary" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/mesa.db",
        "https://secret-canary@mesa.example",
        "http://localhost:invalid",
        "",
    ],
)
def test_invalid_target_fails_before_network_access(probe, monkeypatch, capsys, url):
    module, opener = probe
    monkeypatch.setenv("MESA_BASE_URL", url)
    assert module.main() == 1
    opener.open.assert_not_called()
    assert "secret-canary" not in capsys.readouterr().out


def test_not_ready_stops_before_authenticated_health(probe):
    module, opener = probe
    opener.open.return_value = response(json.dumps({"status": "initializing"}).encode())
    assert module.main() == 1
    assert opener.open.call_count == 1


def test_redirect_handler_refuses_to_forward_credentials(probe):
    module, _ = probe
    request = module.urllib.request.Request(
        "https://mesa.example/health", headers={"X-API-Key": "secret-canary"}
    )
    assert (
        module._NoRedirect().redirect_request(
            request, None, 302, "Found", {}, "https://other.example/health"
        )
        is None
    )
