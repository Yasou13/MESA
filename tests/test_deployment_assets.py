from __future__ import annotations

import sys
from pathlib import Path

import yaml

from mesa_memory.config import RuntimeProfile
from mesa_memory.runtime_entrypoint import command_for_profile

ROOT = Path(__file__).parents[1]


def test_single_container_image_canary_uses_the_self_contained_runtime() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "external-release-gates.yml").read_text(
            encoding="utf-8"
        )
    )
    docker_image = workflow["jobs"]["docker-image"]
    smoke_step = next(
        step
        for step in docker_image["steps"]
        if step.get("name") == "Build, inspect, and smoke the non-root image"
    )
    assert "--env MESA_RUNTIME_PROFILE=combined" in smoke_step["run"]


def test_full_cognitive_compose_forwards_provider_and_tier3_contract() -> None:
    compose = yaml.safe_load(
        (ROOT / "docker-compose.v4.yml").read_text(encoding="utf-8")
    )
    environment = compose["services"]["mesa-v4"]["environment"]
    assert {
        "MESA_LLM_PROVIDER",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL_NAME",
        "LLM_TIMEOUT_SECONDS",
        "MESA_EXTERNAL_PROVIDER_ENABLED",
        "MESA_LOCAL_EMBEDDING_MODEL",
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
        "MESA_TIER3_LLM_PROVIDER_A",
        "MESA_TIER3_LLM_MODEL_A",
        "MESA_TIER3_LLM_PROVIDER_B",
        "MESA_TIER3_LLM_MODEL_B",
        "MESA_OLLAMA_URL",
    } <= set(environment)
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--extra ml --extra adapters" in dockerfile
    assert "find /wheels -maxdepth 1 -name 'mesa_memory-*.whl'" in dockerfile
    assert '"${wheel}[ml,adapters]"' in dockerfile


def test_profile_b_rendered_compose_environment_parity() -> None:
    import os
    import shutil
    import subprocess

    import pytest

    from mesa_memory.config import MesaConfig

    if not shutil.which("docker"):
        pytest.skip("docker binary unavailable in this environment")

    env_overrides = {
        "MESA_API_KEY": "test-mesa-api-key",
        "MESA_PRINCIPAL_ID": "test-principal",
        "MESA_MODEL_ENABLED": "true",
        "MESA_EXTERNAL_PROVIDER_ENABLED": "true",
        "MESA_LLM_PROVIDER": "openai_compatible",
        "LLM_BASE_URL": "https://integrate.api.nvidia.com/v1",
        "LLM_MODEL_NAME": "openai/gpt-oss-20b",
        "LLM_TIMEOUT_SECONDS": "35",
        "LLM_API_KEY": "test-llm-secret-sentinel",
        "MESA_EMBEDDING_PROVIDER": "openai_compatible",
        "MESA_EXTERNAL_EMBEDDING_MODEL": "nvidia/nemotron-3-embed-1b",
        "MESA_EMBEDDING_DIMENSION": "2048",
        "MESA_EMBEDDING_VERSION": "nemotron-qpass-v1",
        "MESA_EMBEDDING_NORMALIZED": "true",
        "MESA_EMBEDDING_BASE_URL": "https://integrate.api.nvidia.com/v1",
        "MESA_EMBEDDING_API_KEY": "test-embed-secret-sentinel",
        "MESA_EMBEDDING_MODEL_REVISION": "nemotron-rev-1",
        "MESA_EXTRACTION_PROVIDER": "openai_compatible",
        "MESA_EXTRACTION_MODEL": "openai/gpt-oss-20b",
        "MESA_EXTRACTION_LANG": "tr",
        "MESA_EXTRACTION_THINKING": "false",
        "MESA_EXTRACTION_MAX_TOKENS": "4096",
    }

    cmd = ["docker", "compose", "-f", str(ROOT / "docker-compose.v4.yml"), "config"]
    res = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env={**os.environ, **env_overrides},
        capture_output=True,
        text=True,
        check=True,
    )
    rendered = yaml.safe_load(res.stdout)
    container_env = rendered["services"]["mesa-v4"]["environment"]

    assert container_env["MESA_LLM_PROVIDER"] == "openai_compatible"
    assert container_env["LLM_BASE_URL"] == "https://integrate.api.nvidia.com/v1"
    assert container_env["LLM_MODEL_NAME"] == "openai/gpt-oss-20b"
    assert container_env["LLM_TIMEOUT_SECONDS"] == "35"
    assert container_env["LLM_API_KEY"] == "test-llm-secret-sentinel"

    assert container_env["MESA_EMBEDDING_PROVIDER"] == "openai_compatible"
    assert (
        container_env["MESA_EXTERNAL_EMBEDDING_MODEL"] == "nvidia/nemotron-3-embed-1b"
    )
    assert container_env["MESA_EMBEDDING_DIMENSION"] == "2048"
    assert container_env["MESA_EMBEDDING_VERSION"] == "nemotron-qpass-v1"
    assert container_env["MESA_EMBEDDING_NORMALIZED"] == "true"
    assert (
        container_env["MESA_EMBEDDING_BASE_URL"]
        == "https://integrate.api.nvidia.com/v1"
    )
    assert container_env["MESA_EMBEDDING_API_KEY"] == "test-embed-secret-sentinel"
    assert container_env["MESA_EMBEDDING_MODEL_REVISION"] == "nemotron-rev-1"

    assert container_env["MESA_EXTRACTION_PROVIDER"] == "openai_compatible"
    assert container_env["MESA_EXTRACTION_MODEL"] == "openai/gpt-oss-20b"
    assert container_env["MESA_EXTRACTION_LANG"] == "tr"
    assert container_env["MESA_EXTRACTION_THINKING"] == "false"
    assert container_env["MESA_EXTRACTION_MAX_TOKENS"] == "4096"

    # Container-inside config validation smoke
    parsed_config = MesaConfig(**container_env)
    assert parsed_config.mesa_llm_provider == "openai_compatible"
    assert parsed_config.llm_base_url == "https://integrate.api.nvidia.com/v1"
    assert parsed_config.llm_model_name == "openai/gpt-oss-20b"
    assert parsed_config.llm_timeout_seconds == 35.0
    assert parsed_config.llm_api_key == "test-llm-secret-sentinel"
    assert parsed_config.embedding_provider == "openai_compatible"
    assert parsed_config.external_embedding_model == "nvidia/nemotron-3-embed-1b"
    assert parsed_config.embedding_dimension == 2048
    assert parsed_config.embedding_version == "nemotron-qpass-v1"
    assert parsed_config.embedding_normalized is True
    assert parsed_config.embedding_base_url == "https://integrate.api.nvidia.com/v1"
    assert parsed_config.embedding_api_key == "test-embed-secret-sentinel"
    assert parsed_config.embedding_model_revision == "nemotron-rev-1"
    assert parsed_config.extraction_provider == "openai_compatible"
    assert parsed_config.extraction_model == "openai/gpt-oss-20b"
    assert parsed_config.extraction_lang == "tr"
    assert parsed_config.extraction_thinking is False
    assert parsed_config.extraction_max_tokens == 4096


def test_dockerfile_uses_exact_base_nonroot_health_and_bounded_entrypoint() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert (
        "python:3.13.12-slim-bookworm@sha256:"
        "a58daefb915e1e03ad48f3ca4df8832065412c5c35cacb9d39f4229184de12b6"
    ) in dockerfile
    assert (
        "ghcr.io/astral-sh/uv:0.13.1@sha256:"
        "4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336"
    ) in dockerfile
    assert "USER mesa:mesa" in dockerfile
    assert "mesa_memory.container_health" in dockerfile
    assert 'ENTRYPOINT ["python", "-m", "mesa_memory.runtime_entrypoint"]' in dockerfile
    assert "COPY . ." not in dockerfile
    assert "MESA_MODEL_ENABLED=false" in dockerfile
    assert "uv.lock" in dockerfile
    assert "uv export" in dockerfile
    assert "--frozen" in dockerfile
    assert "FROM ${PYTHON_IMAGE} AS python-base" in dockerfile
    assert "apt-get upgrade -y --no-install-recommends" in dockerfile


def test_documented_quickstarts_match_the_fail_closed_compose_profile() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (ROOT / "docs/getting-started.md").read_text(encoding="utf-8")

    assert "docs/getting-started.md" in readme
    assert "MESA_PRINCIPAL_ID" in quickstart
    assert "docker compose up --build -d" in quickstart
    assert "docker-compose up" not in quickstart
    assert ".kuzu/" not in readme
    assert "requirements-core.txt" not in readme
    assert "requirements-ml.txt" not in readme
    assert "MESA_MODEL_ENABLED=false" in quickstart
    assert "MESA_EXTERNAL_PROVIDER_ENABLED=false" in quickstart
    assert "503 canonical_processing_unavailable" in quickstart
    assert "MESA_MODEL_ENABLED=true" in quickstart
    assert "infinite out-of-core scaling" not in readme
    assert "returns **202 Accepted** in <50ms" not in readme
    assert "gates every incoming record" not in readme


def test_documented_public_api_examples_match_authenticated_route_contract() -> None:
    quickstart = (ROOT / "docs/getting-started.md").read_text(encoding="utf-8")
    v4_example = (ROOT / "examples/v4_quickstart.py").read_text(encoding="utf-8")

    assert (
        'curl --fail -H "X-API-Key: $MESA_API_KEY" http://127.0.0.1:18080/health'
        in quickstart
    )
    assert "python -m examples.v4_quickstart" in quickstart
    assert "client.wait_until_committed(" in v4_example
    assert "client.end_session(" in v4_example
    assert 'hit.get("provenance", [])' in v4_example


def test_dependency_and_security_governance_assets_are_present() -> None:
    assert (ROOT / "uv.lock").is_file()
    assert (ROOT / "SECURITY.md").is_file()

    dependabot = yaml.safe_load(
        (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    )
    ecosystems = {entry["package-ecosystem"] for entry in dependabot["updates"]}
    assert {"pip", "github-actions", "docker"} <= ecosystems


def test_ci_tests_supported_python_versions_and_enforces_full_repository_lint() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert 'python-version: ["3.11", "3.12", "3.13"]' in workflow
    assert "uv sync --locked --extra dev" in workflow
    assert workflow.count("uv pip check") >= 5
    assert "uv run python -m pip check" not in workflow
    assert "uv run ruff check ." in workflow
    assert "uv run python scripts/check_mypy_override_ratchet.py" in workflow
    assert "mesa_memory mesa_storage mesa_workers mesa_api mesa_client" in workflow


def test_ci_package_job_generates_locked_sbom_and_attests_tagged_artifacts() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert 'tags: ["v*"]' in workflow
    package_job = workflow.split("  package:", maxsplit=1)[1].split(
        "  coverage:", maxsplit=1
    )[0]
    assert "SOURCE_DATE_EPOCH=0 uv build --wheel --out-dir dist/a ." in package_job
    assert "SOURCE_DATE_EPOCH=0 uv build --wheel --out-dir dist/b ." in package_job
    assert "python -m pip wheel" not in package_job
    assert "uv export --quiet --frozen --no-dev --no-emit-project" in workflow
    assert "cyclonedx-py requirements" in workflow
    assert "dist/mesa-runtime.cdx.json" in workflow
    assert (
        "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8"
        in workflow
    )
    assert "if: startsWith(github.ref, 'refs/tags/v')" in package_job
    assert "subject-path:" in package_job
    assert "dist/a/*.whl" in package_job
    assert "attestations: write" in workflow
    assert "id-token: write" in workflow


def test_external_release_gates_use_locked_dependency_sync() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "external-release-gates.yml"
    ).read_text(encoding="utf-8")

    assert workflow.count("uv sync --locked --extra dev") == 3
    assert 'pip install -e ".[dev]"' not in workflow


def test_release_preflight_documents_signed_annotated_tags() -> None:
    script = (ROOT / "scripts" / "release_preflight.py").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "release.md").read_text(encoding="utf-8")

    assert '"git", "cat-file", "-t", tag' in script
    assert '"git", "verify-tag", tag' in script
    assert "git tag -s vX.Y.Z" in runbook
    assert "OIDC build attestations" in runbook


def test_ci_runs_the_full_coverage_suite_on_the_docker_python_version() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    coverage_job = workflow.split("  coverage:", maxsplit=1)[1].split(
        "  optional-integrations:", maxsplit=1
    )[0]
    assert 'python-version: ["3.11", "3.13"]' in coverage_job
    assert "coverage-report-${{ matrix.python-version }}" in coverage_job


def test_mcp_coverage_gates_reject_regressions() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    mcp_coverage = ci.split("  mcp-coverage:", maxsplit=1)[1].split(
        "  zero-cost-contract:", maxsplit=1
    )[0]
    assert "uv sync --locked --extra dev" in mcp_coverage
    assert "--cov=mesa_mcp.v4_service --cov-fail-under=55" in mcp_coverage
    assert "mcp-coverage.xml" in mcp_coverage
    assert "mcp-coverage.json" in mcp_coverage


def test_ci_uses_the_trufflehog_container_tag_and_installs_adapters_for_zero_cost() -> (
    None
):
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "uses: trufflesecurity/trufflehog@e76dff8a7e57d6df0c60ad8cbf5d5cba505a8fc9" in workflow
    assert "version: 3.99.2" in workflow
    assert "ghcr.io/trufflesecurity/trufflehog:3.99.2 --version" in workflow
    assert 'grep -Fx "trufflehog 3.99.2"' in workflow
    assert "extra_args: --results=verified,unknown" in workflow
    assert "fetch-depth: 0" in workflow
    assert "uv sync --locked --extra dev --extra adapters" in workflow


def test_ci_supply_chain_gate_scans_locked_dependencies_and_shipped_images() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    supply_chain = workflow.split("  supply-chain-security:", maxsplit=1)[1].split(
        "  zero-cost-contract:", maxsplit=1
    )[0]

    assert "uv sync --locked --extra dev" in supply_chain
    assert "docker build --pull=false --tag mesa-memory:security" in supply_chain
    assert 'TRIVY_TIMEOUT: "10m"' in supply_chain
    assert (
        supply_chain.count(
            "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25"
        )
        == 4
    )
    assert supply_chain.count("version: v0.70.0") == 4
    assert supply_chain.count('exit-code: "1"') == 2
    assert supply_chain.count("severity: HIGH,CRITICAL") == 2
    assert "format: cyclonedx" in supply_chain
    assert supply_chain.count("if: always()") == 3
    assert "supply-chain-sbom.cdx.json" in supply_chain
    assert "mesa-runtime-image.cdx.json" in supply_chain

    dependabot = yaml.safe_load(
        (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    )
    assert not any(
        u.get("directory") == "/mesa-benchmark/dashboard-ui"
        for u in dependabot["updates"]
    )


def test_docs_smoke_runs_documented_commands_in_the_locked_environment() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "external-release-gates.yml"
    ).read_text(encoding="utf-8")

    docs_smoke = workflow.split("  docs-smoke:", maxsplit=1)[1]
    assert 'uv run python -c "from mesa_memory.runtime_entrypoint' in docs_smoke
    assert "uv run mesa-recovery --help" in docs_smoke


def test_runtime_entrypoint_maps_profiles_without_shell(monkeypatch) -> None:
    from types import SimpleNamespace

    import mesa_memory.runtime_entrypoint as entrypoint

    monkeypatch.setattr(
        entrypoint,
        "load_runtime_profile",
        lambda: SimpleNamespace(
            profile=RuntimeProfile.COMBINED,
            api_enabled=True,
            load_dotenv=False,
        ),
    )
    monkeypatch.setenv("MESA_PORT", "8123")
    assert command_for_profile() == [
        sys.executable, "-m", "uvicorn", "mesa_memory.api.server:app",
        "--host", "0.0.0.0", "--port", "8123",
    ]


def test_active_workflows_keep_the_reviewed_ci_uv_pin() -> None:
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        installs = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if "pip install" in line and "uv==" in line
        ]
        assert installs, path.name
        assert all('"uv==0.13.1"' in line for line in installs), path.name
