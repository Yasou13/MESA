"""Configure and verify the model settings used by the local server."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import warnings
from contextlib import closing
from getpass import GetPassWarning, getpass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key

from .local_cli import resolve_local_paths

_EMBEDDING_MODES = ("none", "local", "ollama", "openai-compatible")
_LLM_MODES = ("none", "openai-compatible")
_LOCAL_MODEL = "local:BAAI/bge-m3"
_LOCAL_MODEL_IGNORE_PATTERNS = (
    "*.onnx",
    "*.onnx_data",
    "onnx/*",
    "*.msgpack",
    "*.h5",
    "*.ot",
    "imgs/*",
    "colbert_linear.pt",
    "sparse_linear.pt",
)
_REBUILD_GUIDE = "docs/v4-rebuild-runbook.md"


class SetupError(RuntimeError):
    """A setup error whose message is safe to print."""


def register_setup_command(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("setup", help="configure and verify local model settings")
    parser.add_argument("--home", type=Path, help="MESA home directory (default: ~/.mesa)")
    parser.add_argument("--storage-root", type=Path, help="storage root checked for vectors")
    parser.add_argument("--non-interactive", action="store_true", help="use flags without prompting")
    parser.add_argument("--embedding", choices=_EMBEDDING_MODES)
    parser.add_argument("--embedding-model", help="embedding model for Ollama or an API")
    parser.add_argument("--embedding-base-url", help="embedding endpoint including /v1")
    parser.add_argument("--llm", choices=_LLM_MODES)
    parser.add_argument("--llm-model", help="chat model at the configured endpoint")
    parser.add_argument("--llm-base-url", help="chat endpoint including /v1")
    parser.add_argument("--yes-rebuild", action="store_true", help="confirm the offline rebuild")
    parser.add_argument("--rebuild-operation-id", help="submitted rebuild operation ID")
    parser.add_argument("--rebuild-work-root", type=Path, help="existing rebuild work directory")
    parser.add_argument("--rebuild-trusted-root", type=Path, help="parent of both storage and work roots")


def _ask(args: argparse.Namespace, attr: str, prompt: str, default: str = "") -> str:
    supplied = getattr(args, attr)
    if supplied is not None:
        return str(supplied).strip()
    if args.non_interactive:
        return default
    suffix = f" [{default}]" if default else ""
    return input(f"{prompt}{suffix}: ").strip() or default


def _key(args: argparse.Namespace, saved_key: str, name: str, label: str) -> str:
    if args.non_interactive:
        return os.environ.get(name, saved_key)
    hint = "blank keeps current" if saved_key else "blank for local endpoints"
    with warnings.catch_warnings():
        warnings.simplefilter("error", GetPassWarning)
        try:
            return getpass(f"{label} API key ({hint}): ").strip() or saved_key
        except GetPassWarning:
            raise SetupError("Use a terminal or pass the API key through the environment.") from None


def _endpoint(value: str, label: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise SetupError(f"Provide a valid {label} HTTP(S) endpoint.") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        raise SetupError(f"Provide a valid {label} HTTP(S) endpoint without credentials.")
    return value.rstrip("/")


def _active_vector_space(storage: Path) -> dict[str, Any] | None:
    database = storage / "mesa.db"
    if not database.is_file():
        return None
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as db:
        registry = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'artifact_registry'"
        ).fetchone()
        if not registry or db.execute(
            "SELECT 1 FROM artifact_registry WHERE store_name = 'VECTOR' AND state = 'ACTIVE' LIMIT 1"
        ).fetchone() is None:
            return None
        row = db.execute(
            "SELECT g.provider_manifest_json FROM projection_runtime r "
            "JOIN projection_generations g ON g.generation_id = r.active_generation_id "
            "WHERE r.runtime_id = 1"
        ).fetchone()
    manifest = json.loads(row[0] or "{}") if row else {}
    if not isinstance(manifest, dict):
        raise SetupError("The active vector generation has invalid provider settings.")
    return manifest


def _model_settings(
    args: argparse.Namespace, current: dict[str, str], has_vectors: bool
) -> dict[str, str]:
    from mesa_memory.adapter.endpoints import is_local_endpoint
    from mesa_memory.adapter.factory import parse_llm_spec
    from mesa_memory.config import (
        NEMOTRON_EMBEDDING_VERSION,
        parse_embedding_spec,
        uses_nemotron_asymmetric_profile,
    )

    embedding = _ask(args, "embedding", f"Embedding ({', '.join(_EMBEDDING_MODES)})", "none")
    llm = _ask(args, "llm", f"LLM ({', '.join(_LLM_MODES)})", "none")
    if embedding not in _EMBEDDING_MODES or llm not in _LLM_MODES:
        raise SetupError("Choose one of the listed embedding and LLM modes.")

    settings = {
        "MESA_EMBEDDING_ENABLED": str(embedding != "none").lower(),
        "MESA_ZERO_COST_MODE": "false",
        "MESA_LLM_MODEL": "",
        "MESA_LLM_BASE_URL": "",
        "MESA_LLM_API_KEY": "",
    }
    if llm != "none":
        model = _ask(args, "llm_model", "Chat model")
        if not model:
            raise SetupError("Provide the chat model with --llm-model.")
        if parse_llm_spec(model)[0] == "mock":
            raise SetupError("Provide a chat model served by your endpoint.")
        url = _endpoint(_ask(args, "llm_base_url", "Chat API base URL"), "chat")
        old_key = (
            current.get("MESA_LLM_API_KEY", "")
            if url == current.get("MESA_LLM_BASE_URL", "").rstrip("/")
            else ""
        )
        settings.update(
            MESA_LLM_MODEL=model,
            MESA_LLM_BASE_URL=url,
            MESA_LLM_API_KEY=_key(args, old_key, "MESA_LLM_API_KEY", "Chat"),
        )
    else:
        settings["MESA_EXTRACTION_ENABLED"] = "false"

    if embedding == "none" and has_vectors:
        # Keep the existing vector identity available for startup fencing.
        provider, _ = parse_embedding_spec(current.get("MESA_EMBEDDING_MODEL", _LOCAL_MODEL))
        if provider != "local":
            settings["MESA_EMBEDDING_API_KEY"] = (
                current.get("MESA_EMBEDDING_API_KEY") or current.get("MESA_LLM_API_KEY", "")
            )
            if provider != "ollama":
                settings["MESA_EMBEDDING_BASE_URL"] = (
                    current.get("MESA_EMBEDDING_BASE_URL")
                    or current.get("MESA_LLM_BASE_URL")
                    or "https://api.openai.com/v1"
                )
    else:
        spec, url, key = _LOCAL_MODEL, "", ""
        if embedding in {"ollama", "openai-compatible"}:
            model = _ask(args, "embedding_model", "Embedding model")
            if not model:
                raise SetupError("Provide the embedding model with --embedding-model.")
            default_url = "http://127.0.0.1:11434/v1" if embedding == "ollama" else ""
            url = _endpoint(
                _ask(args, "embedding_base_url", "Embedding API base URL", default_url),
                "embedding",
            )
            spec = f"{embedding.replace('-', '_')}:{model}"
            old_key = (
                current.get("MESA_EMBEDDING_API_KEY", "")
                if url == current.get("MESA_EMBEDDING_BASE_URL", "").rstrip("/")
                else ""
            )
            key = _key(args, old_key, "MESA_EMBEDDING_API_KEY", "Embedding")
            if not key:
                if is_local_endpoint(url):
                    key = "ollama" if embedding == "ollama" else "local"
                elif url == settings["MESA_LLM_BASE_URL"] and settings["MESA_LLM_API_KEY"]:
                    key = settings["MESA_LLM_API_KEY"]
                else:
                    raise SetupError("Provide MESA_EMBEDDING_API_KEY for the remote embedding endpoint.")
        settings.update(
            MESA_EMBEDDING_MODEL=spec,
            MESA_EMBEDDING_BASE_URL=url,
            MESA_EMBEDDING_API_KEY=key,
            MESA_EMBEDDING_DIMENSION="1024",
        )
        if spec != current.get("MESA_EMBEDDING_MODEL", _LOCAL_MODEL) or embedding == "none":
            version = (
                NEMOTRON_EMBEDDING_VERSION
                if uses_nemotron_asymmetric_profile(*parse_embedding_spec(spec))
                else "v1"
            )
            settings.update(
                MESA_EMBEDDING_VERSION=version,
                MESA_EMBEDDING_NORMALIZED="true",
                MESA_EMBEDDING_MODEL_REVISION="",
            )

    endpoints = (settings.get("MESA_EMBEDDING_BASE_URL"), settings["MESA_LLM_BASE_URL"])
    if any(url and not is_local_endpoint(url) for url in endpoints):
        settings["MESA_EXTERNAL_PROVIDER_ENABLED"] = "true"
    return settings


def _download_local_embedding_model(
    model_name: str, *, revision: str | None = None
) -> None:
    """Download only the files needed by the local SentenceTransformer loader."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise SetupError(
            'Could not complete embedding verification (install mesa-memory[ml]: pip install "mesa-memory[ml]").'
        ) from None

    print(f"Downloading {model_name} to the local Hugging Face cache...", flush=True)
    snapshot_download(
        repo_id=model_name,
        revision=revision or None,
        ignore_patterns=list(_LOCAL_MODEL_IGNORE_PATTERNS),
    )
    print(f"Downloaded {model_name}.", flush=True)


def _verify_models(
    environment: dict[str, str],
    settings: dict[str, str],
    *,
    non_interactive: bool = True,
) -> dict[str, Any] | None:
    from mesa_memory.adapter.factory import AdapterFactory
    from mesa_memory.config import config, refresh_config_from_environment
    from mesa_memory.embedding.service import (
        get_embedding_service,
        set_global_embedding_service,
    )

    original_env = dict(os.environ)
    original_config = config.model_copy()
    logging_level = logging.root.manager.disable
    probe_embedding = settings["MESA_EMBEDDING_ENABLED"] == "true"
    probe_llm = bool(settings["MESA_LLM_MODEL"])
    stage = "model configuration"
    identity = None
    try:
        # A provider's error response or a debug HTTP log can echo credentials.
        logging.disable(sys.maxsize)
        os.environ.update(environment)
        refresh_config_from_environment()
        if probe_embedding:
            stage = "embedding verification"
            is_local = settings["MESA_EMBEDDING_MODEL"].startswith("local:")
            # The dimension probe is also the one real verification request.
            try:
                identity = get_embedding_service(
                    allow_model_loading=True, force_refresh=True, detect_dimension=True
                ).identity()
            except Exception:
                if not is_local:
                    raise
                try:
                    import sentence_transformers  # noqa: F401
                except ImportError:
                    raise SetupError(
                        'Could not complete embedding verification (install mesa-memory[ml]: pip install "mesa-memory[ml]").'
                    ) from None
                model_name = settings["MESA_EMBEDDING_MODEL"].split(":", 1)[1].strip()
                if not non_interactive:
                    answer = (
                        input(
                            f"Local cache for {model_name} is missing. Download now? [Y/n]: "
                        )
                        .strip()
                        .lower()
                    )
                    if answer not in {"", "y", "yes"}:
                        raise SetupError("Local embedding model download declined.")
                stage = f"download of {model_name}"
                _download_local_embedding_model(
                    model_name,
                    revision=settings.get("MESA_EMBEDDING_MODEL_REVISION") or None,
                )
                stage = "embedding verification"
                identity = get_embedding_service(
                    allow_model_loading=True, force_refresh=True, detect_dimension=True
                ).identity()
        if probe_llm:
            stage = "chat verification"
            result = AdapterFactory.get_adapter().chat(
                [{"role": "user", "content": "Reply with OK."}], max_tokens=64
            )
            if not result.text.strip():
                raise ValueError("empty chat result")
    except SetupError:
        raise
    except Exception:
        raise SetupError(f"Could not complete {stage}.") from None
    finally:
        os.environ.clear()
        os.environ.update(original_env)
        for field in type(config).model_fields:
            object.__setattr__(config, field, getattr(original_config, field))
        if probe_embedding:
            set_global_embedding_service(None)
        logging.disable(logging_level)

    if probe_embedding:
        print("Embedding verified.")
    if probe_llm:
        print("Chat model verified.")
    return identity.as_dict() if identity else None


def _rebuild(args: argparse.Namespace, storage: Path, environment: dict[str, str]) -> None:
    print("The active store has vectors in a different embedding space.")
    print(f"Submit and drain an operation with the current server, then stop it. See {_REBUILD_GUIDE}.")
    if not args.yes_rebuild and (
        args.non_interactive or input("Run the offline rebuild now? [y/N]: ").strip().lower() not in {"y", "yes"}
    ):
        raise SetupError("Rebuild declined. Existing settings are still active.")

    operation = _ask(args, "rebuild_operation_id", "Submitted rebuild operation ID")
    work = _ask(args, "rebuild_work_root", "Existing rebuild work directory")
    trusted = _ask(args, "rebuild_trusted_root", "Trusted root", str(storage.resolve().parent))
    if not operation or not work:
        raise SetupError("The rebuild requires --rebuild-operation-id and --rebuild-work-root.")

    result = subprocess.run(
        [
            sys.executable, "-m", "mesa_memory.rebuild_runner", "run",
            "--operation-id", operation,
            "--storage-root", str(storage.resolve()),
            "--trusted-root", str(Path(trusted).expanduser().resolve()),
            "--work-root", str(Path(work).expanduser().resolve()),
        ],
        env=environment | {"MESA_V4_REBUILD_ENABLED": "true"},
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise SetupError(f"Offline rebuild exited with code {result.returncode}. See {_REBUILD_GUIDE}.")
    print("Offline rebuild completed.")


def setup_command(args: argparse.Namespace) -> None:
    try:
        home, storage, _ = resolve_local_paths(home=args.home, storage_root=args.storage_root)
        env_file = home / ".env"
        file_settings = {
            name: value for name, value in dotenv_values(env_file).items() if value is not None
        }
        current = dict(os.environ) | file_settings
        storage = Path(file_settings.get("MESA_STORAGE_ROOT", str(storage))).expanduser()
        if storage.name.endswith(".db"):
            storage = storage.parent
        active_space = _active_vector_space(storage)
        settings = _model_settings(args, current, active_space is not None)

        home.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=home, prefix=".env-setup-", delete=False) as staged:
            staged_path = Path(staged.name)
        try:
            if env_file.exists():
                staged_path.write_bytes(env_file.read_bytes())
            for name, value in settings.items():
                # set_key quotes apostrophes, and the dotenv reader decodes pairs of backslashes.
                set_key(staged_path, name, value.replace("\\", "\\\\"))
            staged_settings = dotenv_values(staged_path)
            if any(staged_settings.get(name) != value for name, value in settings.items()):
                raise SetupError("A setup value could not be stored as a literal dotenv setting.")
            environment = dict(os.environ) | {
                name: value for name, value in staged_settings.items() if value is not None
            }
            target = _verify_models(
                environment, settings, non_interactive=bool(args.non_interactive)
            )
            if target is not None:
                dimension = str(target["dimension"])
                set_key(staged_path, "MESA_EMBEDDING_DIMENSION", dimension)
                environment["MESA_EMBEDDING_DIMENSION"] = dimension
                if active_space is not None and any(
                    active_space.get(name) != value for name, value in target.items()
                ):
                    _rebuild(args, storage, environment)
            staged_path.replace(env_file)
        finally:
            staged_path.unlink(missing_ok=True)
    except SetupError as exc:
        sys.exit(str(exc))
    except (OSError, ValueError, sqlite3.Error, EOFError, KeyboardInterrupt):
        sys.exit("Setup could not read or update the local configuration or store.")
    print(f"Model settings saved to {env_file}")
