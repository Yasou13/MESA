"""Developer test commands must match the documented dependency boundary."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_MARKERS = "not optional_provider and not optional_mcp and not live_external"


def test_makefile_separates_local_and_complete_suites() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "test-local:" in makefile
    assert f'-m "{LOCAL_MARKERS}"' in makefile
    assert "test-all:" in makefile
    assert "test: test-local" in makefile


def test_developer_docs_use_the_locked_local_test_contract() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for document in (readme, contributing):
        assert "uv sync --locked --extra dev" in document
        assert "make test-local" in document
        assert "make test-all" in document
