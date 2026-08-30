"""Regression test suite for Profile B Docker Compose environment parity.

Ensures all provider, model, embedding, and extraction runtime settings
are forwarded from host to container environment without schema or contract drift.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from mesa_memory.config import MesaConfig

ROOT = Path(__file__).parents[1]
COMPOSE_V4_PATH = ROOT / "docker-compose.v4.yml"

REQUIRED_PROFILE_B_ENV_KEYS = {
    "MESA_LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL_NAME",
    "LLM_TIMEOUT_SECONDS",
    "MESA_EXTERNAL_PROVIDER_ENABLED",
    "MESA_EMBEDDING_PROVIDER",
    "MESA_EXTERNAL_EMBEDDING_MODEL",
    "MESA_EMBEDDING_BASE_URL",
    "MESA_EMBEDDING_API_KEY",
    "MESA_EMBEDDING_DIMENSION",
    "MESA_EMBEDDING_VERSION",
    "MESA_EMBEDDING_MODEL_REVISION",
    "MESA_EMBEDDING_NORMALIZED",
    "MESA_EXTRACTION_PROVIDER",
    "MESA_EXTRACTION_MODEL",
    "MESA_EXTRACTION_THINKING",
    "MESA_EXTRACTION_MAX_TOKENS",
    "MESA_EXTRACTION_LANG",
}


def test_static_compose_v4_contains_all_profile_b_env_keys() -> None:
    """Verify statically that docker-compose.v4.yml defines all Profile B runtime keys."""
    raw_compose = yaml.safe_load(COMPOSE_V4_PATH.read_text(encoding="utf-8"))
    runtime_env = raw_compose["x-mesa-v4-runtime"]["environment"]
    service_env = raw_compose["services"]["mesa-v4"]["environment"]

    missing_runtime = REQUIRED_PROFILE_B_ENV_KEYS - set(runtime_env.keys())
    assert not missing_runtime, f"Missing in x-mesa-v4-runtime: {missing_runtime}"

    missing_service = REQUIRED_PROFILE_B_ENV_KEYS - set(service_env.keys())
    assert not missing_service, f"Missing in mesa-v4 service environment: {missing_service}"


def test_effective_compose_config_forwards_profile_b_environment() -> None:
    """Verify that docker compose config renders synthetic Profile B env vars identically."""
    if not shutil.which("docker"):
        pytest.skip("docker binary unavailable in this environment")

    synthetic_env = {
        "MESA_API_KEY": "test-mesa-api-key",
        "MESA_PRINCIPAL_ID": "test-principal-id",
        "MESA_MODEL_ENABLED": "true",
        "MESA_EXTERNAL_PROVIDER_ENABLED": "true",
        "MESA_LLM_PROVIDER": "openai_compatible",
        "LLM_BASE_URL": "https://integrate.api.nvidia.com/v1",
        "LLM_MODEL_NAME": "openai/gpt-oss-20b",
        "LLM_TIMEOUT_SECONDS": "25",
        "LLM_API_KEY": "test-llm-secret-sentinel",
        "MESA_EMBEDDING_PROVIDER": "openai_compatible",
        "MESA_EXTERNAL_EMBEDDING_MODEL": "nvidia/nemotron-3-embed-1b",
        "MESA_EMBEDDING_DIMENSION": "2048",
        "MESA_EMBEDDING_VERSION": "nemotron-qpass-v1",
        "MESA_EMBEDDING_NORMALIZED": "true",
        "MESA_EMBEDDING_BASE_URL": "https://integrate.api.nvidia.com/v1",
        "MESA_EMBEDDING_API_KEY": "test-embed-secret-sentinel",
        "MESA_EMBEDDING_MODEL_REVISION": "nemotron-rev-42",
        "MESA_EXTRACTION_PROVIDER": "openai_compatible",
        "MESA_EXTRACTION_MODEL": "openai/gpt-oss-20b",
        "MESA_EXTRACTION_LANG": "tr",
        "MESA_EXTRACTION_THINKING": "false",
        "MESA_EXTRACTION_MAX_TOKENS": "4096",
    }

    cmd = ["docker", "compose", "-f", str(COMPOSE_V4_PATH), "config"]
    res = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env={**os.environ, **synthetic_env},
        capture_output=True,
        text=True,
        check=True,
    )
    rendered = yaml.safe_load(res.stdout)
    container_env = rendered["services"]["mesa-v4"]["environment"]

    # Verify every synthetic Profile B key forwarded to container
    assert container_env["MESA_LLM_PROVIDER"] == "openai_compatible"
    assert container_env["LLM_BASE_URL"] == "https://integrate.api.nvidia.com/v1"
    assert container_env["LLM_MODEL_NAME"] == "openai/gpt-oss-20b"
    assert container_env["LLM_TIMEOUT_SECONDS"] == "25"
    assert container_env["LLM_API_KEY"] == "test-llm-secret-sentinel"

    assert container_env["MESA_EMBEDDING_PROVIDER"] == "openai_compatible"
    assert container_env["MESA_EXTERNAL_EMBEDDING_MODEL"] == "nvidia/nemotron-3-embed-1b"
    assert container_env["MESA_EMBEDDING_DIMENSION"] == "2048"
    assert container_env["MESA_EMBEDDING_VERSION"] == "nemotron-qpass-v1"
    assert container_env["MESA_EMBEDDING_NORMALIZED"] == "true"
    assert container_env["MESA_EMBEDDING_BASE_URL"] == "https://integrate.api.nvidia.com/v1"
    assert container_env["MESA_EMBEDDING_API_KEY"] == "test-embed-secret-sentinel"
    assert container_env["MESA_EMBEDDING_MODEL_REVISION"] == "nemotron-rev-42"

    assert container_env["MESA_EXTRACTION_PROVIDER"] == "openai_compatible"
    assert container_env["MESA_EXTRACTION_MODEL"] == "openai/gpt-oss-20b"
    assert container_env["MESA_EXTRACTION_LANG"] == "tr"
    assert container_env["MESA_EXTRACTION_THINKING"] == "false"
    assert container_env["MESA_EXTRACTION_MAX_TOKENS"] == "4096"

    # Container-inside config parsing smoke test
    cfg = MesaConfig(**container_env)
    assert cfg.mesa_llm_provider == "openai_compatible"
    assert cfg.llm_base_url == "https://integrate.api.nvidia.com/v1"
    assert cfg.llm_model_name == "openai/gpt-oss-20b"
    assert cfg.llm_timeout_seconds == 25.0
    assert cfg.llm_api_key == "test-llm-secret-sentinel"
    assert cfg.embedding_provider == "openai_compatible"
    assert cfg.external_embedding_model == "nvidia/nemotron-3-embed-1b"
    assert cfg.embedding_dimension == 2048
    assert cfg.embedding_version == "nemotron-qpass-v1"
    assert cfg.embedding_normalized is True
    assert cfg.embedding_base_url == "https://integrate.api.nvidia.com/v1"
    assert cfg.embedding_api_key == "test-embed-secret-sentinel"
    assert cfg.embedding_model_revision == "nemotron-rev-42"
    assert cfg.extraction_provider == "openai_compatible"
    assert cfg.extraction_model == "openai/gpt-oss-20b"
    assert cfg.extraction_lang == "tr"
    assert cfg.extraction_thinking is False
    assert cfg.extraction_max_tokens == 4096


def test_effective_compose_config_defaults_match_mesaconfig_contract() -> None:
    """Verify that when optional host env vars are omitted, rendered defaults align with MesaConfig."""
    if not shutil.which("docker"):
        pytest.skip("docker binary unavailable in this environment")

    minimal_env = {
        "MESA_API_KEY": "test-mesa-api-key",
        "MESA_PRINCIPAL_ID": "test-principal-id",
        "MESA_MODEL_ENABLED": "true",
        "MESA_EXTERNAL_PROVIDER_ENABLED": "false",
    }

    cmd = ["docker", "compose", "-f", str(COMPOSE_V4_PATH), "config"]
    res = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env={**os.environ, **minimal_env},
        capture_output=True,
        text=True,
        check=True,
    )
    rendered = yaml.safe_load(res.stdout)
    container_env = rendered["services"]["mesa-v4"]["environment"]

    # Verify defaults
    assert container_env["MESA_LLM_PROVIDER"] == "openai_compatible"
    assert container_env["LLM_TIMEOUT_SECONDS"] == "20"
    assert container_env["MESA_EMBEDDING_VERSION"] == "v1"
    assert container_env["MESA_EXTRACTION_MAX_TOKENS"] == "4096"
    assert container_env["MESA_EXTRACTION_LANG"] == "tr"

    # Container-inside config smoke
    cfg = MesaConfig(**container_env)
    assert cfg.mesa_llm_provider == "openai_compatible"
    assert cfg.llm_timeout_seconds == 20.0
    assert cfg.embedding_version == "v1"
    assert cfg.extraction_max_tokens == 4096
    assert cfg.extraction_lang == "tr"
