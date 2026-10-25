"""Developer test commands must match the documented dependency boundary."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_MARKERS = "not optional_provider and not optional_mcp and not live_external and not timing"


def test_makefile_separates_local_and_complete_suites() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "test-local:" in makefile
    assert f'-m "{LOCAL_MARKERS}"' in makefile
    assert "test-all:" in makefile
    assert "test: test-local" in makefile


def test_developer_docs_use_the_locked_local_test_contract() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "uv sync --locked --python 3.11" in readme
    assert "docs/testing.md" in readme
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    testing = (REPO_ROOT / "docs/testing.md").read_text(encoding="utf-8")
    for document in (contributing, testing):
        assert "uv sync --locked --python 3.11 --extra dev" in document
        assert "make test-local" in document
        assert "make test-all" in document
