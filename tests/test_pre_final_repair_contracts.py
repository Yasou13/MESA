"""Static and executable contracts for the bounded pre-final repair pass."""

from __future__ import annotations

from pathlib import Path

from mesa_evals import soak_test

ROOT = Path(__file__).parents[1]


def test_production_soak_duration_boundary_is_24_hours() -> None:
    assert not soak_test.is_production_certification_duration(43_199)
    assert not soak_test.is_production_certification_duration(43_200)
    assert not soak_test.is_production_certification_duration(86_399)
    assert soak_test.is_production_certification_duration(86_400)
    assert soak_test.is_production_certification_duration(86_401)


def test_soak_cli_defaults_and_help_describe_24_hours() -> None:
    parser = soak_test._build_parser()

    assert parser.parse_args([]).duration == 86_400
    assert "86400 = 24 hours" in parser.format_help()


def test_direct_dependency_ownership_matches_runtime_imports() -> None:
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dependencies = metadata.split("[project.optional-dependencies]", maxsplit=1)[0]
    loadtest = metadata.split("loadtest = [", maxsplit=1)[1].split("]", maxsplit=1)[0]

    assert '"aiohttp>=3.14.3"' in loadtest
    assert '"sqlalchemy>=2.0.51"' in dependencies
    assert '"typing_extensions>=4.16.0"' in dependencies


def test_v4_docs_consistently_describe_release_candidate_no_go() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs" / "architecture-v4.md").read_text(encoding="utf-8")
    installation = (ROOT / "docs" / "installation.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")
    release = (ROOT / "docs" / "release.md").read_text(encoding="utf-8")

    assert "V0.7.1 is the current v4 full-cognitive release" not in readme
    assert "release candidate" in readme.lower()
    for document in (architecture, installation, runbook, release):
        normalized = " ".join(document.lower().split())
        assert "release candidate" in normalized
        assert "NO-GO" in document


def test_v4_rebel_documentation_names_canonical_extraction_owner() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs" / "architecture-v4.md").read_text(encoding="utf-8")

    for document in (readme, architecture):
        normalized = " ".join(document.split())
        assert "FactExtractionService" in normalized
        lowered = normalized.lower()
        assert "rebel" in lowered
        assert "not part of" in lowered
        assert "canonical v4 extraction" in lowered
    assert "Legacy/V3 CPU-Only REBEL Extraction" in readme
