from __future__ import annotations

from pathlib import Path

import yaml

from mesa_memory.config import RuntimeProfile
from mesa_memory.runtime_entrypoint import command_for_profile

ROOT = Path(__file__).parents[1]


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


def test_dockerfile_uses_exact_base_nonroot_health_and_bounded_entrypoint() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "python:3.13.5-slim-bookworm" in dockerfile
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
    assert "uv run ruff check ." in workflow
    assert "mesa_memory mesa_storage mesa_workers mesa_api mesa_client" in workflow


def test_ci_uses_the_trufflehog_container_tag_and_installs_adapters_for_zero_cost() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "uses: trufflesecurity/trufflehog@v3.95.2" in workflow
    assert "version: 3.95.2" in workflow
    assert 'python -m pip install -e ".[dev,adapters]"' in workflow


def test_runtime_entrypoint_maps_profiles_without_shell(monkeypatch) -> None:
    from types import SimpleNamespace

    import mesa_memory.runtime_entrypoint as entrypoint

    monkeypatch.setattr(
        entrypoint,
        "load_runtime_profile",
        lambda: SimpleNamespace(profile=RuntimeProfile.WORKER_ONLY, api_enabled=False),
    )
    assert command_for_profile() == ["python", "-m", "mesa_memory.worker_runtime"]
    monkeypatch.setattr(
        entrypoint,
        "load_runtime_profile",
        lambda: SimpleNamespace(profile=RuntimeProfile.API_ONLY, api_enabled=True),
    )
    monkeypatch.setenv("MESA_PORT", "8123")
    assert command_for_profile()[-1] == "8123"
