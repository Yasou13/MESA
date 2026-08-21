"""The pytest force-exit workaround must remain explicit and opt-in."""

import importlib.util
from pathlib import Path

TEST_CONFTEST = Path(__file__).with_name("conftest.py")


def _load_test_conftest():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "mesa_test_conftest_contract", TEST_CONFTEST
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_force_exit_compatibility_is_disabled_by_default(monkeypatch) -> None:
    conftest = _load_test_conftest()
    monkeypatch.delenv("MESA_PYTEST_FORCE_EXIT_COMPAT", raising=False)
    assert conftest._force_exit_compat_enabled() is False
    monkeypatch.setenv("MESA_PYTEST_FORCE_EXIT_COMPAT", "1")
    assert conftest._force_exit_compat_enabled() is True


def test_force_exit_contract_has_no_hard_coded_suite_count() -> None:
    source = TEST_CONFTEST.read_text(encoding="utf-8")
    assert "all 918 tests" not in source
    assert "MESA_PYTEST_FORCE_EXIT_COMPAT" in source
