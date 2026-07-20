"""WAVE-005 explicit runtime profile boundary contracts."""
from __future__ import annotations

from pathlib import Path

import pytest

from mesa_memory.config import RuntimeProfile, RuntimeProfileError, load_runtime_profile


def isolated_env(tmp_path, **overrides):
    values = {
        'MESA_RUNTIME_PROFILE': 'test-isolated',
        'MESA_STORAGE_ROOT': str(Path('/storage/mesa-lab/tmp/WAVE-005/runtime-profile-contract')),
        'MESA_LOAD_DOTENV': 'false',
        'MESA_MODEL_ENABLED': 'false',
        'MESA_EXTERNAL_PROVIDER_ENABLED': 'false',
    }
    values.update(overrides)
    return values


def test_invalid_or_missing_profile_fails_closed(tmp_path):
    with pytest.raises(RuntimeProfileError):
        load_runtime_profile({})
    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(isolated_env(tmp_path, MESA_RUNTIME_PROFILE='unknown'))


def test_test_isolated_rejects_dotenv_provider_and_storage_escape(tmp_path):
    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(isolated_env(tmp_path, MESA_LOAD_DOTENV='true'))
    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(isolated_env(tmp_path, MESA_EXTERNAL_PROVIDER_ENABLED='true'))
    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(isolated_env(tmp_path, MESA_STORAGE_ROOT='/tmp/outside'))


def test_api_only_and_worker_only_have_explicit_non_overlapping_roles(tmp_path):
    api = load_runtime_profile(isolated_env(tmp_path, MESA_RUNTIME_PROFILE='api-only'))
    worker = load_runtime_profile(isolated_env(tmp_path, MESA_RUNTIME_PROFILE='worker-only'))
    assert api.profile is RuntimeProfile.API_ONLY
    assert api.api_enabled is True and api.worker_enabled is False
    assert worker.profile is RuntimeProfile.WORKER_ONLY
    assert worker.api_enabled is False and worker.worker_enabled is True


def test_test_isolated_never_reads_dotenv_without_explicit_allowance(tmp_path):
    env = isolated_env(tmp_path)
    profile = load_runtime_profile(env)
    assert profile.load_dotenv is False
    assert profile.model_enabled is False
    assert profile.external_provider_enabled is False


def test_test_isolated_default_runtime_lab_root_is_accepted(tmp_path):
    profile = load_runtime_profile(isolated_env(tmp_path))
    assert profile.storage_root.is_relative_to(Path('/storage/mesa-lab'))


def test_test_isolated_accepts_explicit_runtime_lab_root(tmp_path):
    lab_root = tmp_path / 'mesa-lab'
    storage_root = lab_root / 'storage'
    lab_root.mkdir()
    profile = load_runtime_profile(
        isolated_env(
            tmp_path,
            MESA_RUNTIME_LAB_ROOT=str(lab_root),
            MESA_STORAGE_ROOT=str(storage_root),
        )
    )
    assert profile.storage_root == storage_root.resolve()


def test_test_isolated_rejects_runtime_lab_root_escapes_and_relative_paths(tmp_path):
    lab_root = tmp_path / 'mesa-lab'
    lab_root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    sibling = tmp_path / 'mesa-lab-sibling' / 'storage'

    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(
            isolated_env(
                tmp_path,
                MESA_RUNTIME_LAB_ROOT=str(lab_root),
                MESA_STORAGE_ROOT=str(outside / 'storage'),
            )
        )
    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(
            isolated_env(
                tmp_path,
                MESA_RUNTIME_LAB_ROOT=str(lab_root),
                MESA_STORAGE_ROOT=str(sibling),
            )
        )
    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(
            isolated_env(
                tmp_path,
                MESA_RUNTIME_LAB_ROOT='relative-lab',
                MESA_STORAGE_ROOT=str(lab_root / 'storage'),
            )
        )
    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(
            isolated_env(
                tmp_path,
                MESA_RUNTIME_LAB_ROOT=str(lab_root),
                MESA_STORAGE_ROOT='relative-storage',
            )
        )


def test_test_isolated_rejects_symlink_escape_from_runtime_lab_root(tmp_path):
    lab_root = tmp_path / 'mesa-lab'
    lab_root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (lab_root / 'storage').symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(
            isolated_env(
                tmp_path,
                MESA_RUNTIME_LAB_ROOT=str(lab_root),
                MESA_STORAGE_ROOT=str(lab_root / 'storage'),
            )
        )
