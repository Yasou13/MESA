from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "check_mypy_override_ratchet.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("mypy_ratchet", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checked_in_progressive_overrides_do_not_expand() -> None:
    module = _load_module()
    assert (
        module.validate(
            ROOT / "pyproject.toml",
            ROOT / "typing" / "mypy-progressive-overrides.json",
        )
        == []
    )


def test_new_progressive_module_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    changed = config.replace(
        '    "mesa_workers.*",',
        '    "mesa_workers.*",\n    "mesa_memory.new_untyped_module",',
    )
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(changed, encoding="utf-8")

    errors = module.validate(
        config_path,
        ROOT / "typing" / "mypy-progressive-overrides.json",
    )
    assert errors == ["new progressive mypy modules: mesa_memory.new_untyped_module"]


def test_new_relaxed_option_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    changed = config.replace(
        "warn_unused_ignores = false",
        "warn_unused_ignores = false\ncheck_untyped_defs = false",
        1,
    )
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(changed, encoding="utf-8")

    errors = module.validate(
        config_path,
        ROOT / "typing" / "mypy-progressive-overrides.json",
    )
    assert errors == ["new relaxed mypy options: check_untyped_defs"]
