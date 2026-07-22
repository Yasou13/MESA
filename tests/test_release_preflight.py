from __future__ import annotations

import importlib.util
from pathlib import Path

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
