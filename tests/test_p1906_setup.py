"""CLI setup verification and rebuild consent."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import stat
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values

from mesa_mcp import cli
from mesa_mcp import setup as setup_cli
from mesa_memory.embedding.service import EmbeddingIdentity
from tests.fake_openai_server import FakeOpenAIServer, chat_completion_payload

_CHAT_KEY = "fake-chat-key-\\'with-dollar$"
_EMBEDDING_KEY = "fake-embedding-key-91"


def _run(monkeypatch, home: Path, *flags: str, interactive: bool = False) -> None:
    arguments = ["mesa", "setup", "--home", str(home), "--storage-root", str(home / "data")]
    if not interactive:
        arguments.append("--non-interactive")
    monkeypatch.setattr(sys, "argv", [*arguments, *flags])
    cli.main()


def _embedding_args(server: FakeOpenAIServer, model: str = "embed-v2") -> list[str]:
    return [
        "--embedding", "openai-compatible", "--embedding-model", model,
        "--embedding-base-url", server.base_url,
    ]


def _chat_args(server: FakeOpenAIServer) -> list[str]:
    return [
        "--llm", "openai-compatible", "--llm-model", "chat-v2",
        "--llm-base-url", server.base_url,
    ]


def _fake_models(path, headers, body):
    if path == "/v1/embeddings":
        assert headers["authorization"] == f"Bearer {_EMBEDDING_KEY}"
        assert body["model"] == "embed-v2"
        assert body["input"]
        return 200, {
            "object": "list",
            "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0, 0.0]}],
            "model": body["model"],
            "usage": {"prompt_tokens": 7, "total_tokens": 7},
        }
    assert path == "/v1/chat/completions"
    assert headers["authorization"] == f"Bearer {_CHAT_KEY}"
    assert body["model"] == "chat-v2"
    return 200, chat_completion_payload("OK", model=body["model"])


def test_no_models_creates_start_env_and_preserves_unrelated_settings(tmp_path, monkeypatch):
    home = tmp_path / "mesa"
    _run(monkeypatch, home, "--embedding", "none", "--llm", "none")
    env_file = home / ".env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    with env_file.open("a") as stream:
        stream.write("# custom configuration\nMESA_CHUNKING=plain\n")
    _run(monkeypatch, home, "--embedding", "none", "--llm", "none")

    settings = dotenv_values(env_file)
    assert settings["MESA_EMBEDDING_ENABLED"] == "false"
    assert settings["MESA_LLM_MODEL"] == ""
    assert settings["MESA_CHUNKING"] == "plain"
    assert "# custom configuration" in env_file.read_text()

    async def fake_provision(*_args):
        pass

    monkeypatch.setattr("mesa_mcp.local_cli.provision_credentials", fake_provision)
    monkeypatch.setattr("uvicorn.run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sys, "argv", ["mesa", "start", "--home", str(home)])
    for name in ("MESA_RUNTIME_PROFILE", "MESA_STORAGE_ROOT", "MESA_MODEL_ENABLED",
                 "MESA_LOAD_DOTENV", "MESA_DOTENV_PATH"):
        monkeypatch.delenv(name, raising=False)
    cli.main()
    assert os.environ["MESA_DOTENV_PATH"] == str(env_file.resolve())


def test_local_embedding_only_probes_bge_m3_once(tmp_path, monkeypatch, capsys):
    calls = []

    class FakeSentenceTransformer:
        def __init__(self, model, **options):
            assert model == "BAAI/bge-m3"
            assert options == {"local_files_only": True}

        def encode(self, text, **options):
            calls.append(text)
            assert options == {"normalize_embeddings": True, "show_progress_bar": False}
            return [1.0, 2.0, 3.0]

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=FakeSentenceTransformer)
    )
    _run(monkeypatch, tmp_path, "--embedding", "local")
    settings = dotenv_values(tmp_path / ".env")
    assert settings["MESA_EMBEDDING_ENABLED"] == "true"
    assert settings["MESA_EMBEDDING_MODEL"] == "local:BAAI/bge-m3"
    assert settings["MESA_EMBEDDING_DIMENSION"] == "3"
    assert settings["MESA_LLM_MODEL"] == ""
    assert len(calls) == 1
    assert "Embedding verified." in capsys.readouterr().out


@pytest.mark.parametrize("with_embedding", [False, True])
def test_chat_only_or_both_make_one_call_per_model(
    tmp_path, monkeypatch, capsys, caplog, with_embedding
):
    monkeypatch.setenv("MESA_LLM_API_KEY", _CHAT_KEY)
    monkeypatch.setenv("MESA_EMBEDDING_API_KEY", _EMBEDDING_KEY)
    caplog.set_level(logging.DEBUG)
    with FakeOpenAIServer(_fake_models) as server:
        flags = _embedding_args(server) if with_embedding else []
        _run(monkeypatch, tmp_path, *flags, *_chat_args(server))
        paths = [request["path"] for request in server.requests]

    assert paths == (["/v1/embeddings"] if with_embedding else []) + ["/v1/chat/completions"]
    settings = dotenv_values(tmp_path / ".env")
    assert settings["MESA_LLM_API_KEY"] == _CHAT_KEY
    assert settings["MESA_EMBEDDING_ENABLED"] == str(with_embedding).lower()
    assert settings["MESA_EMBEDDING_API_KEY"] == (_EMBEDDING_KEY if with_embedding else "")
    captured = capsys.readouterr()
    for secret in (_CHAT_KEY, _EMBEDDING_KEY):
        assert secret not in captured.out + captured.err + caplog.text


def test_probe_failure_preserves_existing_file_and_redacts_provider_error(
    tmp_path, monkeypatch, capsys, caplog
):
    monkeypatch.setenv("MESA_LLM_API_KEY", _CHAT_KEY)
    env_file = tmp_path / ".env"
    original = b"# keep this file\nMESA_CHUNKING=plain\n"
    env_file.write_bytes(original)
    caplog.set_level(logging.DEBUG)
    with FakeOpenAIServer(lambda *_args: (401, {"error": {"message": _CHAT_KEY}})) as server:
        with pytest.raises(SystemExit, match="Could not complete chat verification"):
            _run(monkeypatch, tmp_path, *_chat_args(server))
        assert len(server.requests) == 1
    assert env_file.read_bytes() == original
    assert list(tmp_path.glob(".env-setup-*")) == []
    captured = capsys.readouterr()
    assert _CHAT_KEY not in captured.out + captured.err + caplog.text


def test_local_ollama_does_not_receive_the_chat_key(tmp_path, monkeypatch):
    monkeypatch.setenv("MESA_LLM_API_KEY", _CHAT_KEY)
    monkeypatch.delenv("MESA_EMBEDDING_API_KEY", raising=False)

    def embedding_model(path, headers, body):
        assert headers["authorization"] == "Bearer ollama"
        return _fake_models(path, {"authorization": f"Bearer {_EMBEDDING_KEY}"}, body)

    with FakeOpenAIServer(embedding_model) as embeddings, FakeOpenAIServer(_fake_models) as chat:
        _, _, *options = _embedding_args(embeddings)
        _run(monkeypatch, tmp_path, "--embedding", "ollama", *options, *_chat_args(chat))
    assert len(embeddings.requests) == len(chat.requests) == 1
    assert dotenv_values(tmp_path / ".env")["MESA_EMBEDDING_MODEL"] == "ollama:embed-v2"


def _vector_store(home: Path, identity: EmbeddingIdentity) -> Path:
    root = home / "data"
    root.mkdir()
    with closing(sqlite3.connect(root / "mesa.db")) as db:
        db.executescript(
            "CREATE TABLE artifact_registry (store_name TEXT, state TEXT);"
            "INSERT INTO artifact_registry VALUES ('VECTOR', 'ACTIVE');"
            "CREATE TABLE projection_runtime (runtime_id INTEGER, active_generation_id TEXT);"
            "INSERT INTO projection_runtime VALUES (1, 'active');"
            "CREATE TABLE projection_generations (generation_id TEXT, provider_manifest_json TEXT);"
        )
        db.execute(
            "INSERT INTO projection_generations VALUES ('active', ?)",
            (json.dumps(identity.as_dict()),),
        )
        db.commit()
    return root


def test_embedding_change_offers_rebuild_and_requires_consent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MESA_EMBEDDING_API_KEY", _EMBEDDING_KEY)
    root = _vector_store(tmp_path, EmbeddingIdentity("local", "BAAI/bge-m3", 1024))
    env_file = tmp_path / ".env"
    original = b"MESA_EMBEDDING_MODEL=local:BAAI/bge-m3\nMESA_EMBEDDING_ENABLED=true\n"
    env_file.write_bytes(original)
    prompts = []

    def decline(prompt):
        prompts.append(prompt)
        return ""

    def unexpected_rebuild(*_args, **_kwargs):
        pytest.fail("setup launched a rebuild without consent")

    monkeypatch.setattr("builtins.input", decline)
    monkeypatch.setattr(setup_cli, "getpass", lambda _prompt: _EMBEDDING_KEY)
    monkeypatch.setattr(setup_cli.subprocess, "run", unexpected_rebuild)
    with FakeOpenAIServer(_fake_models) as server:
        flags = [*_embedding_args(server), "--llm", "none"]
        with pytest.raises(SystemExit, match="Rebuild declined"):
            _run(monkeypatch, tmp_path, *flags, interactive=True)
        assert prompts == ["Run the offline rebuild now? [y/N]: "]
        with pytest.raises(SystemExit, match="Rebuild declined"):
            _run(monkeypatch, tmp_path, *flags)
        assert len(server.requests) == 2
    assert "v4-rebuild-runbook.md" in capsys.readouterr().out
    assert env_file.read_bytes() == original
    assert list(root.iterdir()) == [root / "mesa.db"]


def test_confirmed_rebuild_uses_verified_target_and_saves_only_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("MESA_EMBEDDING_API_KEY", _EMBEDDING_KEY)
    storage = _vector_store(tmp_path, EmbeddingIdentity("local", "BAAI/bge-m3", 1024))
    work = tmp_path / "work"
    work.mkdir()
    env_file = tmp_path / ".env"
    original = b"MESA_EMBEDDING_MODEL=local:BAAI/bge-m3\n"
    env_file.write_bytes(original)
    exit_codes = iter([4, 0])

    def rebuild(command, *, env, capture_output, check):
        assert command[1:4] == ["-m", "mesa_memory.rebuild_runner", "run"]
        assert command[4:] == [
            "--operation-id", "operation", "--storage-root", str(storage),
            "--trusted-root", str(tmp_path), "--work-root", str(work),
        ]
        assert env["MESA_EMBEDDING_MODEL"] == "openai_compatible:embed-v2"
        assert env["MESA_EMBEDDING_DIMENSION"] == "3"
        assert env["MESA_EMBEDDING_API_KEY"] == _EMBEDDING_KEY
        assert capture_output and not check
        assert env_file.read_bytes() == original
        return SimpleNamespace(returncode=next(exit_codes))

    monkeypatch.setattr(setup_cli.subprocess, "run", rebuild)
    with FakeOpenAIServer(_fake_models) as server:
        flags = [
            *_embedding_args(server), "--yes-rebuild", "--rebuild-operation-id", "operation",
            "--rebuild-work-root", str(work),
        ]
        with pytest.raises(SystemExit, match="Offline rebuild exited with code 4"):
            _run(monkeypatch, tmp_path, *flags)
        assert env_file.read_bytes() == original
        _run(monkeypatch, tmp_path, *flags)
    assert dotenv_values(env_file)["MESA_EMBEDDING_MODEL"] == "openai_compatible:embed-v2"
