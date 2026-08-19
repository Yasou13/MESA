from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "release_preflight.py"


def test_release_preflight_rejects_non_semantic_tag_before_git_checks() -> None:
    spec = importlib.util.spec_from_file_location("release_preflight", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.validate("release-candidate") == [
        "tag must use the vMAJOR.MINOR.PATCH format"
    ]


@pytest.fixture
def release_preflight_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("release_preflight_metadata", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_metadata_rejects_wrong_tag_version(
    release_preflight_module, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.7.1"\n', encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text("## [0.7.1]\n", encoding="utf-8")
    release_preflight_module.ROOT = tmp_path

    assert release_preflight_module.validate_metadata("v0.7.2") == [
        "tag version does not match pyproject.toml",
        "CHANGELOG.md has no matching release heading",
    ]


def test_release_metadata_rejects_missing_changelog_heading(
    release_preflight_module, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.7.1"\n', encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n", encoding="utf-8")
    release_preflight_module.ROOT = tmp_path

    assert release_preflight_module.validate_metadata("v0.7.1") == [
        "CHANGELOG.md has no matching release heading"
    ]


def test_release_metadata_accepts_matching_version_and_changelog(
    release_preflight_module, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.7.1"\n', encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text("## [0.7.1]\n", encoding="utf-8")
    release_preflight_module.ROOT = tmp_path

    assert release_preflight_module.validate_metadata("v0.7.1") == []


def test_tag_package_job_requires_ci_metadata_preflight() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    preflight_job = workflow.split("  release-preflight:", maxsplit=1)[1].split(
        "  quality:", maxsplit=1
    )[0]
    package_job = workflow.split("  package:", maxsplit=1)[1].split(
        "  coverage:", maxsplit=1
    )[0]

    assert "if: startsWith(github.ref, 'refs/tags/v')" in preflight_job
    assert (
        'python scripts/release_preflight.py --metadata-only "$GITHUB_REF_NAME"'
        in preflight_job
    )
    assert "needs: [quality, release-preflight]" in package_job
