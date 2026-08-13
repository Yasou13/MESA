from mesa_memory.config import (
    load_explicit_dotenv,
    load_runtime_profile,
    refresh_config_from_environment,
)


def test_config_storage_root_aliases(tmp_path):
    """Verify that MESA_STORAGE_ROOT, MESA_STORAGE_PATH, MESA_STORAGE_DIR, and MESA_DB_PATH map into one canonical storage root."""
    target_dir = (tmp_path / "mesa_storage_canonical").resolve()

    # 1. MESA_STORAGE_ROOT
    env1 = {"MESA_RUNTIME_PROFILE": "combined", "MESA_STORAGE_ROOT": str(target_dir)}
    cfg1 = load_runtime_profile(env1)
    assert cfg1.storage_root == target_dir

    # 2. MESA_STORAGE_PATH
    env2 = {"MESA_RUNTIME_PROFILE": "combined", "MESA_STORAGE_PATH": str(target_dir)}
    cfg2 = load_runtime_profile(env2)
    assert cfg2.storage_root == target_dir

    # 3. MESA_STORAGE_DIR
    env3 = {"MESA_RUNTIME_PROFILE": "combined", "MESA_STORAGE_DIR": str(target_dir)}
    cfg3 = load_runtime_profile(env3)
    assert cfg3.storage_root == target_dir

    # 4. MESA_DB_PATH (points to mesa.db inside target_dir)
    db_file = target_dir / "mesa.db"
    env4 = {"MESA_RUNTIME_PROFILE": "combined", "MESA_DB_PATH": str(db_file)}
    cfg4 = load_runtime_profile(env4)
    assert cfg4.storage_root == target_dir


def test_explicit_dotenv_is_loaded_before_the_active_profile_is_reparsed(tmp_path):
    dotenv = tmp_path / "runtime.env"
    storage = tmp_path / "from-dotenv"
    dotenv.write_text(
        f"MESA_STORAGE_ROOT={storage}\nMESA_MODEL_ENABLED=true\n",
        encoding="utf-8",
    )
    bootstrap = load_runtime_profile(
        {
            "MESA_RUNTIME_PROFILE": "combined",
            "MESA_STORAGE_ROOT": str(tmp_path / "bootstrap"),
            "MESA_LOAD_DOTENV": "true",
            "MESA_DOTENV_PATH": str(dotenv),
        }
    )
    # ``load_explicit_dotenv`` intentionally writes only missing values; use
    # a fresh process env contract here through the runtime helper itself.
    import os

    old = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(
            {
                "MESA_RUNTIME_PROFILE": "combined",
                "MESA_STORAGE_ROOT": str(tmp_path / "bootstrap"),
                "MESA_LOAD_DOTENV": "true",
                "MESA_DOTENV_PATH": str(dotenv),
            }
        )
        load_explicit_dotenv(bootstrap)
        refresh_config_from_environment()
        active = load_runtime_profile()
        assert active.storage_root == storage
        assert active.model_enabled is True
    finally:
        os.environ.clear()
        os.environ.update(old)
