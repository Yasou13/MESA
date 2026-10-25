"""WAVE-005 explicit runtime profile boundary contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from mesa_memory.config import (
    REMOVED_MESA_ENV_VARS,
    RuntimeProfile,
    RuntimeProfileError,
    load_runtime_profile,
)


def isolated_env(tmp_path, **overrides):
    values = {
        "MESA_RUNTIME_PROFILE": "test-isolated",
        "MESA_STORAGE_ROOT": str(
            Path("/storage/mesa-lab/tmp/WAVE-005/runtime-profile-contract")
        ),
        "MESA_LOAD_DOTENV": "false",
        "MESA_MODEL_ENABLED": "false",
        "MESA_EXTERNAL_PROVIDER_ENABLED": "false",
    }
    values.update(overrides)
    return values


def test_invalid_or_missing_profile_fails_closed(tmp_path):
    with pytest.raises(RuntimeProfileError):
        load_runtime_profile({})
    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(isolated_env(tmp_path, MESA_RUNTIME_PROFILE="unknown"))


def test_test_isolated_rejects_dotenv_provider_and_storage_escape(tmp_path):
    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(isolated_env(tmp_path, MESA_LOAD_DOTENV="true"))
    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(
            isolated_env(tmp_path, MESA_EXTERNAL_PROVIDER_ENABLED="true")
        )
    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(isolated_env(tmp_path, MESA_STORAGE_ROOT="/tmp/outside"))


def test_test_isolated_never_reads_dotenv_without_explicit_allowance(tmp_path):
    env = isolated_env(tmp_path)
    profile = load_runtime_profile(env)
    assert profile.load_dotenv is False
    assert profile.model_enabled is False
    assert profile.external_provider_enabled is False


def test_test_isolated_default_runtime_lab_root_is_accepted(tmp_path):
    profile = load_runtime_profile(isolated_env(tmp_path))
    assert profile.storage_root.is_relative_to(Path("/storage/mesa-lab"))


def test_test_isolated_accepts_explicit_runtime_lab_root(tmp_path):
    lab_root = tmp_path / "mesa-lab"
    storage_root = lab_root / "storage"
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
    lab_root = tmp_path / "mesa-lab"
    lab_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sibling = tmp_path / "mesa-lab-sibling" / "storage"

    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(
            isolated_env(
                tmp_path,
                MESA_RUNTIME_LAB_ROOT=str(lab_root),
                MESA_STORAGE_ROOT=str(outside / "storage"),
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
                MESA_RUNTIME_LAB_ROOT="relative-lab",
                MESA_STORAGE_ROOT=str(lab_root / "storage"),
            )
        )
    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(
            isolated_env(
                tmp_path,
                MESA_RUNTIME_LAB_ROOT=str(lab_root),
                MESA_STORAGE_ROOT="relative-storage",
            )
        )


def test_test_isolated_rejects_symlink_escape_from_runtime_lab_root(tmp_path):
    lab_root = tmp_path / "mesa-lab"
    lab_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (lab_root / "storage").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(
            isolated_env(
                tmp_path,
                MESA_RUNTIME_LAB_ROOT=str(lab_root),
                MESA_STORAGE_ROOT=str(lab_root / "storage"),
            )
        )


def test_combined_enables_api_and_workers(tmp_path):
    runtime = load_runtime_profile(
        isolated_env(tmp_path, MESA_RUNTIME_PROFILE="combined")
    )
    assert runtime.profile is RuntimeProfile.COMBINED
    assert runtime.api_enabled is True and runtime.worker_enabled is True


@pytest.mark.parametrize("flag", ["MESA_EMBEDDING_ENABLED", "MESA_EXTRACTION_ENABLED"])
def test_test_isolated_forbids_separate_model_flags(tmp_path, flag):
    with pytest.raises(RuntimeProfileError):
        load_runtime_profile(isolated_env(tmp_path, **{flag: "true"}))


@pytest.mark.parametrize("legacy", ["true", "false"])
def test_separate_flags_default_to_legacy_and_can_override_it(tmp_path, legacy):
    env = isolated_env(
        tmp_path, MESA_RUNTIME_PROFILE="combined", MESA_MODEL_ENABLED=legacy
    )
    legacy_bool = legacy == "true"
    runtime = load_runtime_profile(env)
    assert runtime.embedding_enabled is legacy_bool
    assert runtime.extraction_enabled is legacy_bool
    override_str = "false" if legacy_bool else "true"
    runtime_embed_only = load_runtime_profile(
        {**env, "MESA_EMBEDDING_ENABLED": override_str}
    )
    assert runtime_embed_only.embedding_enabled is (not legacy_bool)
    assert runtime_embed_only.extraction_enabled is legacy_bool
    runtime_extract_only = load_runtime_profile(
        {**env, "MESA_EXTRACTION_ENABLED": override_str}
    )
    assert runtime_extract_only.embedding_enabled is legacy_bool
    assert runtime_extract_only.extraction_enabled is (not legacy_bool)
    runtime = load_runtime_profile(
        {**env, "MESA_EMBEDDING_ENABLED": "false", "MESA_EXTRACTION_ENABLED": "true"}
    )
    assert runtime.embedding_enabled is False and runtime.extraction_enabled is True


@pytest.mark.parametrize("removed_var", REMOVED_MESA_ENV_VARS)
def test_removed_mesa_env_vars_stop_startup_with_message(tmp_path, removed_var):
    env = isolated_env(tmp_path, **{removed_var: "any_value"})
    with pytest.raises(RuntimeProfileError) as exc_info:
        load_runtime_profile(env)
    msg = str(exc_info.value)
    assert removed_var in msg
    replacement = "MESA_EMBEDDING_MODEL" if "EMBEDDING" in removed_var else "MESA_LLM_MODEL"
    assert replacement in msg
    assert "e.g." in msg or "example" in msg.lower()


def test_unprefixed_env_vars_never_fail_startup(tmp_path):
    env = isolated_env(
        tmp_path,
        LLM_BASE_URL="https://answering.example.com",
        LLM_API_KEY="secret-llm-key",
        LLM_MODEL_NAME="custom-model",
        LLM_TIMEOUT_SECONDS="45",
    )
    # Unprefixed names must not raise RuntimeProfileError
    runtime = load_runtime_profile(env)
    assert runtime is not None
