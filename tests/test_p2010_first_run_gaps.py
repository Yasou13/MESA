"""D120 first-run gap tests: dataset creator permissions and local model download."""

from __future__ import annotations

import builtins
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from mesa_mcp import cli
from mesa_mcp.local_cli import provision_credentials
from mesa_memory.api import server


def _configure_server_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MESA_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("MESA_RUNTIME_PROFILE", "combined")
    monkeypatch.setenv("MESA_LOAD_DOTENV", "false")
    monkeypatch.setenv("MESA_MODEL_ENABLED", "false")
    monkeypatch.setenv("MESA_EMBEDDING_ENABLED", "false")
    monkeypatch.setenv("MESA_EXTRACTION_ENABLED", "false")
    monkeypatch.setenv("MESA_EXTERNAL_PROVIDER_ENABLED", "false")
    monkeypatch.setenv("MESA_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("MESA_API_KEY", "d120-bootstrap-key")
    monkeypatch.setenv("MESA_PRINCIPAL_ID", "owner-one")


def test_new_dataset_grants_creator_purge_and_rollback_but_repost_grants_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_server_env(monkeypatch, tmp_path)
    with TestClient(server.app) as client:
        async def _setup_principals() -> tuple[str, str]:
            key_one = await server.state.api_key_store.issue_key(
                principal_id="owner-one"
            )
            key_two = await server.state.api_key_store.issue_key(
                principal_id="owner-two"
            )
            for pid in ("owner-one", "owner-two"):
                await server.state.access_control.grant_scope_role(
                    pid, tenant_id="local", role="OWNER"
                )
                await server.state.access_control.grant_principal_permission(
                    pid, "local-agent", "SESSION_CREATE"
                )
            return key_one, key_two

        key_one, key_two = client.portal.call(_setup_principals)
        h1 = {"X-API-Key": key_one}
        h2 = {"X-API-Key": key_two}

        assert (
            client.post(
                "/v4/catalog/workspaces",
                headers=h1,
                json={"tenant_id": "local", "workspace_id": "default"},
            ).status_code
            == 201
        )
        # First POST creates the dataset -> owner-one receives PURGE and ROLLBACK.
        assert (
            client.post(
                "/v4/catalog/datasets",
                headers=h1,
                json={
                    "tenant_id": "local",
                    "workspace_id": "default",
                    "dataset_id": "collection-alpha",
                },
            ).status_code
            == 201
        )

        # Second POST on the existing dataset by another OWNER grants NOTHING.
        assert (
            client.post(
                "/v4/catalog/datasets",
                headers=h2,
                json={
                    "tenant_id": "local",
                    "workspace_id": "default",
                    "dataset_id": "collection-alpha",
                },
            ).status_code
            == 201
        )
        assert (
            client.portal.call(
                lambda: server.state.access_control.check_dataset_permission(
                    "owner-one",
                    tenant_id="local",
                    dataset_id="collection-alpha",
                    permission="PURGE",
                )
            )
            is True
        )
        assert (
            client.portal.call(
                lambda: server.state.access_control.check_dataset_permission(
                    "owner-one",
                    tenant_id="local",
                    dataset_id="collection-alpha",
                    permission="ROLLBACK",
                )
            )
            is True
        )
        assert (
            client.portal.call(
                lambda: server.state.access_control.check_dataset_permission(
                    "owner-two",
                    tenant_id="local",
                    dataset_id="collection-alpha",
                    permission="PURGE",
                )
            )
            is False
        )
        assert (
            client.portal.call(
                lambda: server.state.access_control.check_dataset_permission(
                    "owner-two",
                    tenant_id="local",
                    dataset_id="collection-alpha",
                    permission="ROLLBACK",
                )
            )
            is False
        )

        # Ingest a document into collection-alpha and wait for commit.
        ingest = client.post(
            "/v4/documents",
            headers=h1,
            json={
                "tenant_id": "local",
                "workspace_id": "default",
                "dataset_id": "collection-alpha",
                "document_id": "doc-alpha-1",
                "title": "Alpha Doc",
                "source_ref": "file://alpha.txt",
                "content": "Collection alpha contract terms.",
            },
        )
        assert ingest.status_code == 202
        mut_id = ingest.json()["mutation_ids"][0]
        for _ in range(60):
            status = client.get(f"/v4/mutations/{mut_id}", headers=h1).json()
            if status.get("state") == "COMMITTED":
                break
            time.sleep(0.05)

        # owner-two cannot purge (403 PURGE permission required) despite re-posting.
        denied_purge = client.delete(
            "/v4/catalog/documents/doc-alpha-1",
            headers=h2,
            params={
                "tenant_id": "local",
                "workspace_id": "default",
                "dataset_id": "collection-alpha",
            },
        )
        assert denied_purge.status_code == 403
        assert "PURGE" in denied_purge.json()["detail"]

        # owner-one (creator) can purge the document immediately.
        allowed_purge = client.delete(
            "/v4/catalog/documents/doc-alpha-1",
            headers=h1,
            params={
                "tenant_id": "local",
                "workspace_id": "default",
                "dataset_id": "collection-alpha",
            },
        )
        assert allowed_purge.status_code == 202


@pytest.mark.asyncio
async def test_default_dataset_provisioning_unchanged(tmp_path: Path) -> None:
    home = tmp_path / "mesa"
    storage = tmp_path / "data"
    creds = await provision_credentials(home, storage, port=8777)
    assert creds["dataset_id"] == "default"
    from mesa_memory.security.rbac import AccessControl

    ac = AccessControl(str(storage / "rbac_policy.db"))
    await ac.initialize()
    try:
        assert await ac.check_dataset_permission(
            "local-admin", tenant_id="local", dataset_id="default", permission="PURGE"
        )
        assert await ac.check_dataset_permission(
            "local-admin",
            tenant_id="local",
            dataset_id="default",
            permission="ROLLBACK",
        )
    finally:
        await ac.close()


def _run_setup(
    monkeypatch: pytest.MonkeyPatch, home: Path, *flags: str, interactive: bool = False
) -> None:
    args = ["mesa", "setup", "--home", str(home), "--storage-root", str(home / "data")]
    if not interactive:
        args.append("--non-interactive")
    monkeypatch.setattr(sys, "argv", [*args, *flags])
    cli.main()


def test_setup_local_embedding_downloads_missing_cache_and_verifies_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state = {"downloaded": False, "downloads": [], "load_attempts": 0}

    class FakeSentenceTransformer:
        def __init__(self, model: str, **options: Any) -> None:
            assert model == "BAAI/bge-m3"
            # Runtime loading must always stay offline (local_files_only=True).
            assert options == {"local_files_only": True}
            state["load_attempts"] += 1
            if not state["downloaded"]:
                raise OSError("LocalEntryNotFoundError: model not cached")

        def encode(self, _text: str, **_options: Any) -> list[float]:
            return [0.25, 0.5, 0.75, 1.0]

    def fake_snapshot_download(
        *, repo_id: str, revision: str | None = None, ignore_patterns: list[str] | None = None
    ) -> str:
        assert repo_id == "BAAI/bge-m3"
        assert revision is None
        assert ignore_patterns and "*.onnx" in ignore_patterns
        state["downloaded"] = True
        state["downloads"].append(repo_id)
        return str(tmp_path / "hf-cache")

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=fake_snapshot_download),
    )

    _run_setup(monkeypatch, tmp_path, "--embedding", "local")
    out = capsys.readouterr().out
    assert state["downloads"] == ["BAAI/bge-m3"]
    assert state["load_attempts"] == 2
    assert "Downloading BAAI/bge-m3 to the local Hugging Face cache..." in out
    assert "Downloaded BAAI/bge-m3." in out
    assert "Embedding verified." in out
    saved = dotenv_values(tmp_path / ".env")
    assert saved["MESA_EMBEDDING_ENABLED"] == "true"
    assert saved["MESA_EMBEDDING_MODEL"] == "local:BAAI/bge-m3"
    assert saved["MESA_EMBEDDING_DIMENSION"] == "4"


def test_setup_local_embedding_interactive_prompts_and_respects_decline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloads: list[str] = []

    class MissingCacheST:
        def __init__(self, _model: str, **options: Any) -> None:
            assert options == {"local_files_only": True}
            raise OSError("not cached")

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=MissingCacheST),
    )
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=lambda **kw: downloads.append(kw["repo_id"])),
    )
    prompts = iter(["local", "none", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(prompts))

    with pytest.raises(SystemExit, match="download declined"):
        _run_setup(monkeypatch, tmp_path, interactive=True)
    assert downloads == []
    assert not (tmp_path / ".env").exists()


def test_setup_local_embedding_without_ml_extra_prints_install_hint_and_does_not_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloads: list[str] = []
    real_import = builtins.__import__

    def blocking_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "sentence_transformers" or name.startswith("sentence_transformers."):
            raise ImportError("No module named 'sentence_transformers'")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=lambda **kw: downloads.append(kw["repo_id"])),
    )
    monkeypatch.setattr(builtins, "__import__", blocking_import)

    with pytest.raises(SystemExit, match=r"mesa-memory\[ml\]"):
        _run_setup(monkeypatch, tmp_path, "--embedding", "local")
    assert downloads == []
    assert not (tmp_path / ".env").exists()
