"""Production structured-logging contract tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_python(source: str, **environment: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "MESA_LOAD_DOTENV": "false",
            "MESA_MODEL_ENABLED": "false",
            "MESA_EXTERNAL_PROVIDER_ENABLED": "false",
            "MESA_LOG_FORMAT": "json",
            "MESA_LOG_LEVEL": "INFO",
            **environment,
        }
    )
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _json_lines(result: subprocess.CompletedProcess[str]) -> list[dict]:
    assert result.returncode == 0, result.stderr
    assert not result.stderr.strip(), result.stderr
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def test_stdlib_and_structlog_share_the_v1_json_schema():
    result = _run_python(
        "from mesa_memory.observability.logger import setup_logging; "
        "import logging, structlog; "
        "setup_logging(role='worker'); "
        "logging.getLogger('stdlib').info('stdlib_event'); "
        "structlog.get_logger('structured').info('structured_event', operation_id='7')"
    )

    records = _json_lines(result)
    assert len(records) == 2
    for record in records:
        assert record["schema_version"] == 1
        assert record["service"] == "mesa"
        assert record["role"] == "worker"
        assert record["timestamp"].endswith("Z")
        assert {"level", "logger", "event"} <= record.keys()


def test_observability_event_is_emitted_once_without_nested_json():
    result = _run_python(
        "from mesa_memory.observability.logger import setup_logging; "
        "setup_logging(role='api'); "
        "from mesa_memory.observability.metrics import ObservabilityLayer; "
        "ObservabilityLayer().log_consolidation_batch('batch-1', 2, 0, 2, 1.5)"
    )

    records = [r for r in _json_lines(result) if r.get("batch_id") == "batch-1"]
    assert len(records) == 1
    assert records[0]["event"] == "consolidation_batch"


def test_bootstrap_replaces_prior_handlers_and_is_idempotent():
    result = _run_python(
        "import logging; "
        "logging.basicConfig(); "
        "from mesa_memory.observability.logger import setup_logging; "
        "setup_logging(role='api'); "
        "setup_logging(role='api'); "
        "logging.getLogger('contract').info('one_event'); "
        "assert len(logging.getLogger().handlers) == 1"
    )

    records = _json_lines(result)
    assert [record["event"] for record in records] == ["one_event"]


def test_sensitive_fields_and_exception_messages_are_redacted():
    result = _run_python(
        "from mesa_memory.observability.logger import setup_logging; "
        "import logging, structlog; "
        "setup_logging(role='api'); "
        "structlog.get_logger('safe').warning('safe_event', "
        "query='AUDIT_QUERY_SENTINEL', content='AUDIT_CONTENT_SENTINEL', "
        "nested={'api_key': 'AUDIT_KEY_SENTINEL', "
        "'openai_api_key': 'AUDIT_OPENAI_KEY_SENTINEL', "
        "'access_token': 'AUDIT_ACCESS_TOKEN_SENTINEL', "
        "'client_secret': 'AUDIT_CLIENT_SECRET_SENTINEL'}, "
        "token_count=17); "
        "logger=logging.getLogger('safe'); "
        "\ntry:\n raise RuntimeError('AUDIT_EXCEPTION_SENTINEL')"
        "\nexcept RuntimeError as exc:\n logger.exception('operation failed: %s', exc)"
    )

    records = _json_lines(result)
    serialized = json.dumps(records)
    for sentinel in (
        "AUDIT_QUERY_SENTINEL",
        "AUDIT_CONTENT_SENTINEL",
        "AUDIT_KEY_SENTINEL",
        "AUDIT_OPENAI_KEY_SENTINEL",
        "AUDIT_ACCESS_TOKEN_SENTINEL",
        "AUDIT_CLIENT_SECRET_SENTINEL",
        "AUDIT_EXCEPTION_SENTINEL",
    ):
        assert sentinel not in serialized
    assert records[0]["query"] == "[REDACTED]"
    assert records[0]["nested"]["api_key"] == "[REDACTED]"
    assert records[0]["nested"]["openai_api_key"] == "[REDACTED]"
    assert records[0]["nested"]["access_token"] == "[REDACTED]"
    assert records[0]["nested"]["client_secret"] == "[REDACTED]"
    assert records[0]["token_count"] == 17
    assert records[1]["exception_type"] == "RuntimeError"


@pytest.mark.parametrize(
    ("environment", "value", "error_name"),
    [
        ("MESA_LOG_LEVEL", "VERBOSE", "MESA_LOG_LEVEL"),
        ("MESA_LOG_FORMAT", "xml", "MESA_LOG_FORMAT"),
    ],
)
def test_invalid_logging_configuration_fails_fast_without_starting_the_process(
    environment: str, value: str, error_name: str
):
    result = _run_python(
        "from mesa_memory.observability.logger import setup_logging; setup_logging()",
        **{environment: value},
    )

    assert result.returncode != 0
    assert error_name in result.stderr


def test_configured_log_level_filters_lower_severity_events():
    result = _run_python(
        "from mesa_memory.observability.logger import setup_logging; "
        "import logging; "
        "setup_logging(role='worker'); "
        "logger=logging.getLogger('level-contract'); "
        "logger.info('hidden_event'); "
        "logger.error('visible_event')",
        MESA_LOG_LEVEL="ERROR",
    )

    records = _json_lines(result)
    assert [record["event"] for record in records] == ["visible_event"]


def test_compose_bounds_logs_and_sets_production_defaults():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    runtime = compose["x-mesa-runtime"]
    assert runtime["logging"] == {
        "driver": "local",
        "options": {"max-size": "10m", "max-file": "5"},
    }
    environment = runtime["environment"]
    assert environment["MESA_LOG_LEVEL"] == "${MESA_LOG_LEVEL:-INFO}"
    assert environment["MESA_LOG_FORMAT"] == "${MESA_LOG_FORMAT:-json}"


@pytest.mark.parametrize(
    "relative_path",
    [
        "mesa_memory/runtime_entrypoint.py",
        "mesa_memory/worker_runtime.py",
        "mesa_memory/api/server.py",
    ],
)
def test_process_entrypoints_bootstrap_before_runtime_config(relative_path: str):
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    assert source.index("setup_logging") < source.index("from mesa_memory.config")
