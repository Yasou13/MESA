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


def test_fresh_mesa_home_lists_default_dataset_without_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    home = tmp_path / "fresh_home"
    storage = home / "data"
    creds = asyncio.run(provision_credentials(home, storage, port=8799))

    monkeypatch.setenv("MESA_STORAGE_ROOT", str(storage))
    monkeypatch.setenv("MESA_RUNTIME_PROFILE", "combined")
    monkeypatch.setenv("MESA_LOAD_DOTENV", "false")
    monkeypatch.setenv("MESA_MODEL_ENABLED", "false")
    monkeypatch.setenv("MESA_EMBEDDING_ENABLED", "false")
    monkeypatch.setenv("MESA_EXTRACTION_ENABLED", "false")
    monkeypatch.setenv("MESA_EXTERNAL_PROVIDER_ENABLED", "false")
    monkeypatch.setenv("MESA_RATE_LIMIT_ENABLED", "false")
    monkeypatch.delenv("MESA_API_KEY", raising=False)
    monkeypatch.delenv("MESA_PRINCIPAL_ID", raising=False)

    headers = {"X-API-Key": creds["api_key"]}
    with TestClient(server.app) as client:
        resp = client.get(
            "/v4/catalog/datasets",
            headers=headers,
            params={
                "tenant_id": creds["tenant_id"],
                "workspace_id": creds["workspace_id"],
            },
        )
        assert resp.status_code == 200, resp.text
        dataset_ids = [d["dataset_id"] for d in resp.json()["datasets"]]
        assert "default" in dataset_ids

        # Unknown workspace returns empty list, never 500.
        unknown = client.get(
            "/v4/catalog/datasets",
            headers=headers,
            params={"tenant_id": creds["tenant_id"], "workspace_id": "nonexistent"},
        )
        assert unknown.status_code == 200
        assert unknown.json() == {"datasets": []}


def test_existing_home_without_catalog_scope_lists_default_after_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A home made by an older `mesa start` (credentials, schema, no catalog rows)."""
    import asyncio
    import sqlite3

    from mesa_mcp.local_cli import _issue_or_reuse_credentials
    from mesa_storage.schemas import initialize_schema
    from mesa_storage.sqlite_engine import AsyncEngine

    home = tmp_path / "old_home"
    storage = home / "data"

    async def _old_start() -> dict[str, Any]:
        creds = await _issue_or_reuse_credentials(home, storage, 8797)
        engine = AsyncEngine(str(storage / "mesa.db"))
        await engine.initialize()
        try:
            await initialize_schema(engine)
        finally:
            await engine.close()
        return creds

    old_creds = asyncio.run(_old_start())
    with sqlite3.connect(storage / "mesa.db") as db:
        assert db.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 0

    creds = asyncio.run(provision_credentials(home, storage, port=8797))
    assert creds["api_key"] == old_creds["api_key"]

    monkeypatch.setenv("MESA_STORAGE_ROOT", str(storage))
    monkeypatch.setenv("MESA_RUNTIME_PROFILE", "combined")
    monkeypatch.setenv("MESA_LOAD_DOTENV", "false")
    monkeypatch.setenv("MESA_MODEL_ENABLED", "false")
    monkeypatch.setenv("MESA_EMBEDDING_ENABLED", "false")
    monkeypatch.setenv("MESA_EXTRACTION_ENABLED", "false")
    monkeypatch.setenv("MESA_EXTERNAL_PROVIDER_ENABLED", "false")
    monkeypatch.setenv("MESA_RATE_LIMIT_ENABLED", "false")
    monkeypatch.delenv("MESA_API_KEY", raising=False)
    monkeypatch.delenv("MESA_PRINCIPAL_ID", raising=False)

    with TestClient(server.app) as client:
        resp = client.get(
            "/v4/catalog/datasets",
            headers={"X-API-Key": creds["api_key"]},
            params={"tenant_id": "local", "workspace_id": "default"},
        )
    assert resp.status_code == 200, resp.text
    assert "default" in [d["dataset_id"] for d in resp.json()["datasets"]]


def test_upload_stores_source_ref_on_document_external_ref_across_restart_failure_and_existing_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    home = tmp_path / "upload_home"
    storage = home / "data"
    creds = asyncio.run(provision_credentials(home, storage, port=8798))

    monkeypatch.setenv("MESA_STORAGE_ROOT", str(storage))
    monkeypatch.setenv("MESA_RUNTIME_PROFILE", "combined")
    monkeypatch.setenv("MESA_LOAD_DOTENV", "false")
    monkeypatch.setenv("MESA_MODEL_ENABLED", "false")
    monkeypatch.setenv("MESA_EMBEDDING_ENABLED", "false")
    monkeypatch.setenv("MESA_EXTRACTION_ENABLED", "false")
    monkeypatch.setenv("MESA_EXTERNAL_PROVIDER_ENABLED", "false")
    monkeypatch.setenv("MESA_RATE_LIMIT_ENABLED", "false")
    monkeypatch.delenv("MESA_API_KEY", raising=False)
    monkeypatch.delenv("MESA_PRINCIPAL_ID", raising=False)

    headers = {"X-API-Key": creds["api_key"]}
    base_params = {
        "tenant_id": "local",
        "workspace_id": "default",
        "dataset_id": "default",
    }

    with TestClient(server.app) as client:
        # 1. Normal upload sets document.external_ref from source_ref.
        up = client.post(
            "/v4/documents/upload",
            headers=headers,
            files={"file": ("lease.txt", b"Article 1: Lease term is one year.", "text/plain")},
            data={
                **base_params,
                "document_id": "doc-uploaded",
                "title": "Lease Agreement",
                "source_ref": "file:///cases/lease.txt",
            },
        )
        assert up.status_code == 202, up.text
        mut_id = up.json()["mutation_ids"][0]
        for _ in range(60):
            if client.get(f"/v4/mutations/{mut_id}", headers=headers).json().get("state") == "COMMITTED":
                break
            time.sleep(0.05)

        doc_status = client.get("/v4/documents/doc-uploaded", headers=headers, params=base_params)
        assert doc_status.status_code == 200
        assert doc_status.json()["external_ref"] == "file:///cases/lease.txt"

        doc_list = client.get("/v4/catalog/documents", headers=headers, params=base_params)
        assert doc_list.status_code == 200
        by_id = {d["document_id"]: d for d in doc_list.json()["documents"]}
        assert by_id["doc-uploaded"]["external_ref"] == "file:///cases/lease.txt"

        # 2. Pre-created document with caller-set external_ref must NOT be overwritten.
        pre = client.post(
            "/v4/catalog/documents",
            headers=headers,
            json={
                **base_params,
                "document_id": "doc-caller-ref",
                "title": "Caller Ref Doc",
                "external_ref": "custom://caller-owned-ref",
            },
        )
        assert pre.status_code == 201
        up2 = client.post(
            "/v4/documents/upload",
            headers=headers,
            files={"file": ("override.txt", b"Uploaded content body.", "text/plain")},
            data={
                **base_params,
                "document_id": "doc-caller-ref",
                "title": "Caller Ref Doc",
                "source_ref": "file:///cases/override.txt",
            },
        )
        assert up2.status_code == 202, up2.text
        mut2 = up2.json()["mutation_ids"][0]
        for _ in range(60):
            if client.get(f"/v4/mutations/{mut2}", headers=headers).json().get("state") == "COMMITTED":
                break
            time.sleep(0.05)

        caller_doc = client.get("/v4/documents/doc-caller-ref", headers=headers, params=base_params).json()
        assert caller_doc["external_ref"] == "custom://caller-owned-ref"

        # Chunk still retains the upload's source_ref.
        search = client.post(
            "/v4/memory/search",
            headers=headers,
            json={
                "tenant_id": "local",
                "workspace_id": "default",
                "dataset_ids": ["default"],
                "document_ids": ["doc-caller-ref"],
                "query": "Uploaded content body",
            },
        )
        assert search.status_code == 200
        hits = search.json()["results"]
        assert hits
        assert hits[0]["provenance"][0]["source_ref"] == "file:///cases/override.txt"

        # 3. Document whose revision later fails still keeps its uploaded external_ref.
        up_fail = client.post(
            "/v4/documents/upload",
            headers=headers,
            files={"file": ("broken.txt", b"Text for a failing revision.", "text/plain")},
            data={
                **base_params,
                "document_id": "doc-failed",
                "title": "Failing Doc",
                "source_ref": "file:///cases/broken.txt",
            },
        )
        assert up_fail.status_code == 202, up_fail.text
        fail_mut_id = up_fail.json()["mutation_ids"][0]
        for _ in range(60):
            if client.get(f"/v4/mutations/{fail_mut_id}", headers=headers).json().get("state") == "COMMITTED":
                break
            time.sleep(0.05)

        async def _force_failed_state() -> None:
            async with server.state.sqlite_engine.transaction() as db:
                await db.execute(
                    "UPDATE memory_mutations SET state = 'DEAD_LETTER', failure_class = 'ProjectionError' "
                    "WHERE mutation_id = ?",
                    (fail_mut_id,),
                )
                await db.execute(
                    "UPDATE document_revisions SET status = 'PENDING' "
                    "WHERE revision_id = (SELECT revision_id FROM memory_mutations WHERE mutation_id = ?)",
                    (fail_mut_id,),
                )
                await db.commit()

        client.portal.call(_force_failed_state)
        failed_doc = client.get("/v4/documents/doc-failed", headers=headers, params=base_params).json()
        assert failed_doc["external_ref"] == "file:///cases/broken.txt"
        assert failed_doc["revisions"][0]["stage"] == "failed"

    # 4. Restart server and verify external_ref persists for all uploaded documents.
    with TestClient(server.app) as restarted:
        after_upload = restarted.get(
            "/v4/documents/doc-uploaded", headers=headers, params=base_params
        ).json()
        assert after_upload["external_ref"] == "file:///cases/lease.txt"

        after_failed = restarted.get(
            "/v4/documents/doc-failed", headers=headers, params=base_params
        ).json()
        assert after_failed["external_ref"] == "file:///cases/broken.txt"

        after_list = restarted.get(
            "/v4/catalog/documents", headers=headers, params=base_params
        ).json()["documents"]
        refs = {d["document_id"]: d["external_ref"] for d in after_list}
        assert refs["doc-uploaded"] == "file:///cases/lease.txt"
        assert refs["doc-failed"] == "file:///cases/broken.txt"
        assert refs["doc-caller-ref"] == "custom://caller-owned-ref"

