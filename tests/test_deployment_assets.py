from __future__ import annotations

import sys
from pathlib import Path

import yaml

from mesa_memory.config import RuntimeProfile
from mesa_memory.runtime_entrypoint import command_for_profile
from scripts.run_server import _dashboard_static_file

ROOT = Path(__file__).parents[1]


def test_dashboard_static_files_cannot_escape_dist_root(tmp_path: Path) -> None:
    dashboard = tmp_path / "dist"
    dashboard.mkdir()
    asset = dashboard / "favicon.svg"
    asset.write_text("safe", encoding="utf-8")
    showcase = dashboard / "showcase"
    showcase.mkdir()
    showcase_index = showcase / "index.html"
    showcase_index.write_text("showcase", encoding="utf-8")
    secret = tmp_path / "outside.txt"
    secret.write_text("not public", encoding="utf-8")
    linked_directory = dashboard / "linked"
    linked_directory.mkdir()
    (linked_directory / "index.html").symlink_to(secret)

    assert _dashboard_static_file(str(dashboard), "favicon.svg") == str(asset)
    assert _dashboard_static_file(str(dashboard), "showcase/") == str(showcase_index)
    assert _dashboard_static_file(str(dashboard), "linked/") is None
    assert _dashboard_static_file(str(dashboard), "%2e%2e/outside.txt") is None
    assert _dashboard_static_file(str(dashboard), "../outside.txt") is None


def test_showcase_assets_live_under_dashboard_without_mock_console() -> None:
    showcase = ROOT / "mesa_dashboard" / "public" / "showcase"
    expected_assets = {
        "index.html",
        "scripts/showcase.js",
        "styles/showcase.css",
        "styles/tokens.css",
        "visualizer/index.html",
        "visualizer/visualizer.css",
        "visualizer/visualizer.js",
    }

    assert not (ROOT / "demo").exists()
    assert {
        str(path.relative_to(showcase))
        for path in showcase.rglob("*")
        if path.is_file()
    } == expected_assets

    landing = (showcase / "index.html").read_text(encoding="utf-8")
    assert 'href="/dashboard/"' in landing
    assert "/dashboard/showcase/scripts/showcase.js" in landing
    assert "/dashboard/showcase/visualizer/" in landing
    assert "console/index.html" not in landing


def test_runtime_wheel_constrains_pyod_numba_for_supported_python() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project_metadata, separator, _ = pyproject.partition(
        "[project.optional-dependencies]"
    )

    assert separator, "project optional-dependencies section is missing"
    assert '"pyod>=3.3.0"' in project_metadata
    assert '"numba>=0.65.0"' in project_metadata
    assert '"llvmlite>=0.47.0"' in project_metadata


def test_compose_has_isolated_api_and_worker_roles_without_host_bind_or_dotenv(
    monkeypatch,
) -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert set(compose["services"]) == {"mesa-api", "mesa-worker"}
    for service in compose["services"].values():
        assert "env_file" not in service
        assert service["read_only"] is True
        assert service["volumes"] == ["mesa-data:/var/lib/mesa"]
        assert service["environment"]["MESA_MODEL_ENABLED"] == "false"
        assert service["environment"]["MESA_EXTERNAL_PROVIDER_ENABLED"] == "false"
    assert (
        compose["services"]["mesa-api"]["environment"]["MESA_RUNTIME_PROFILE"]
        == "api-only"
    )
    assert (
        compose["services"]["mesa-api"]["environment"]["MESA_REQUIRE_WORKER_READINESS"]
        == "true"
    )
    assert (
        compose["services"]["mesa-worker"]["environment"]["MESA_RUNTIME_PROFILE"]
        == "worker-only"
    )


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
        "LLM_EMBEDDING_MODEL",
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


def test_dockerfile_uses_exact_base_nonroot_health_and_bounded_entrypoint() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert (
        "python:3.13.5-slim-bookworm@sha256:"
        "4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419"
    ) in dockerfile
    assert (
        "ghcr.io/astral-sh/uv:0.11.30@sha256:"
        "93b61e21202b1dab861092748e46bbd6e0e41dd84f59b9174efd2353186e1b47"
    ) in dockerfile
    assert "USER mesa:mesa" in dockerfile
    assert "mesa_memory.container_health" in dockerfile
    assert 'ENTRYPOINT ["python", "-m", "mesa_memory.runtime_entrypoint"]' in dockerfile
    assert "COPY . ." not in dockerfile
    assert "MESA_MODEL_ENABLED=false" in dockerfile
    assert "uv.lock" in dockerfile
    assert "uv export" in dockerfile
    assert "--frozen" in dockerfile


def test_readme_compose_quickstart_matches_the_fail_closed_compose_profile() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "MESA_PRINCIPAL_ID" in readme
    assert "docker compose up --build -d" in readme
    assert "docker-compose up" not in readme
    assert ".kuzu/" not in readme
    assert "requirements-core.txt" not in readme
    assert "requirements-ml.txt" not in readme
    assert "MESA_MODEL_ENABLED=false" in readme
    assert "MESA_EXTERNAL_PROVIDER_ENABLED=false" in readme
    assert "Safe core" in readme
    assert "Full cognitive runtime" in readme
    assert "infinite out-of-core scaling" not in readme
    assert "returns **202 Accepted** in <50ms" not in readme
    assert "gates every incoming record" not in readme


def test_readme_public_api_examples_match_authenticated_route_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert (
        'curl --fail -H "X-API-Key: $MESA_API_KEY" http://localhost:8000/health'
        in readme
    )
    assert "/v3/memory/status/1?agent_id=analyst_1" in readme
    assert "/v3/memory/session/start" in readme
    assert "/v3/session/start" not in readme


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

    assert 'python-version: ["3.10", "3.11", "3.12", "3.13"]' in workflow
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
        "actions/attest-build-provenance@e8998f949152b193b063cb0ec769d69d929409be"
        in workflow
    )
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
    assert 'python-version: ["3.10", "3.13"]' in coverage_job
    assert "coverage-report-${{ matrix.python-version }}" in coverage_job


def test_mcp_and_benchmark_coverage_gates_reject_regressions() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    mcp_coverage = ci.split("  mcp-coverage:", maxsplit=1)[1].split(
        "  zero-cost-contract:", maxsplit=1
    )[0]
    assert "uv sync --locked --extra dev --extra mcp" in mcp_coverage
    assert "--cov=mesa_mcp.v4_service --cov-fail-under=55" in mcp_coverage
    assert "mcp-coverage.xml" in mcp_coverage
    assert "mcp-coverage.json" in mcp_coverage

    benchmark_workflow = (
        ROOT / ".github" / "workflows" / "benchmark-quality.yml"
    ).read_text(encoding="utf-8")
    assert "--cov=mesa_benchmark.dashboard --cov-fail-under=54" in benchmark_workflow
    assert "benchmark-coverage.xml" in benchmark_workflow
    assert "benchmark-coverage.json" in benchmark_workflow
    assert "npm run test:coverage" in benchmark_workflow

    frontend_config = (
        ROOT / "mesa-benchmark" / "dashboard-ui" / "vite.config.ts"
    ).read_text(encoding="utf-8")
    assert 'provider: "v8"' in frontend_config
    assert "lines: 10" in frontend_config
    assert "branches: 30" in frontend_config


def test_ci_uses_the_trufflehog_container_tag_and_installs_adapters_for_zero_cost() -> (
    None
):
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "uses: trufflesecurity/trufflehog@v3.95.2" in workflow
    assert "version: 3.95.2" in workflow
    assert "uv sync --locked --extra dev --extra adapters" in workflow


def test_ci_supply_chain_gate_scans_locked_dependencies_and_benchmark_image() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    supply_chain = workflow.split("  supply-chain-security:", maxsplit=1)[1].split(
        "  zero-cost-contract:", maxsplit=1
    )[0]

    assert "uv sync --locked --extra dev --extra benchmarks" in supply_chain
    assert "npm ci --ignore-scripts" in supply_chain
    assert "npm audit --omit=dev --audit-level=high" in supply_chain
    assert "docker build --pull=false --tag mesa-benchmark:security" in supply_chain
    assert (
        supply_chain.count(
            "aquasecurity/trivy-action@57a97c7e7821a5776cebc9bb87c984fa69cba8f1"
        )
        == 3
    )
    assert supply_chain.count('exit-code: "1"') == 2
    assert supply_chain.count("severity: HIGH,CRITICAL") == 2
    assert "format: cyclonedx" in supply_chain
    assert "supply-chain-sbom.cdx.json" in supply_chain

    dependabot = yaml.safe_load(
        (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    )
    assert {
        "package-ecosystem": "npm",
        "directory": "/mesa-benchmark/dashboard-ui",
        "schedule": {"interval": "weekly"},
    } in dependabot["updates"]


def test_docs_smoke_runs_documented_commands_in_the_locked_environment() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "external-release-gates.yml"
    ).read_text(encoding="utf-8")

    docs_smoke = workflow.split("  docs-smoke:", maxsplit=1)[1]
    assert 'uv run python -c "from mesa_memory.runtime_entrypoint' in docs_smoke
    assert 'uv run python -c "from mesa_memory.worker_runtime' in docs_smoke
    assert "uv run mesa-recovery --help" in docs_smoke


def test_benchmark_workflow_defers_runner_temp_resolution_to_a_step() -> None:
    workflow = (ROOT / ".github" / "workflows" / "benchmark-quality.yml").read_text(
        encoding="utf-8"
    )

    assert "BENCHMARK_JUDGE_CALIBRATION_PATH: ${{ runner.temp }}" not in workflow
    assert (
        'echo "BENCHMARK_JUDGE_CALIBRATION_PATH=$RUNNER_TEMP/judge-calibration.json" '
        '>> "$GITHUB_ENV"'
    ) in workflow
    assert "timeout-minutes: 720" not in workflow


def test_runtime_entrypoint_maps_profiles_without_shell(monkeypatch) -> None:
    from types import SimpleNamespace

    import mesa_memory.runtime_entrypoint as entrypoint

    monkeypatch.setattr(
        entrypoint,
        "load_runtime_profile",
        lambda: SimpleNamespace(
            profile=RuntimeProfile.WORKER_ONLY,
            api_enabled=False,
            load_dotenv=False,
        ),
    )
    assert command_for_profile() == [sys.executable, "-m", "mesa_memory.worker_runtime"]
    monkeypatch.setattr(
        entrypoint,
        "load_runtime_profile",
        lambda: SimpleNamespace(
            profile=RuntimeProfile.API_ONLY,
            api_enabled=True,
            load_dotenv=False,
        ),
    )
    monkeypatch.setenv("MESA_PORT", "8123")
    assert command_for_profile()[-1] == "8123"
