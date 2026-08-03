"""Offline rebuild CLI ownership, orchestration and exit-code contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import mesa_storage.rebuild_runner as runner
from mesa_storage.rebuild_cutover import RebuildCutoverResult
from mesa_storage.rebuild_preparation import RebuildPreparation
from mesa_storage.rebuild_replay import RebuildInterruptedError, RebuildReplayResult
from mesa_storage.writer_lock import StorageWriterLock

_OPERATION_ID = "11111111-2222-4333-8444-555555555555"


def _roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    trusted = tmp_path / "trusted"
    storage = trusted / "storage"
    work = trusted / "work"
    storage.mkdir(parents=True)
    work.mkdir()
    return trusted, storage, work


def _arguments(trusted: Path, storage: Path, work: Path) -> list[str]:
    return [
        "run",
        "--trusted-root",
        str(trusted),
        "--storage-root",
        str(storage),
        "--work-root",
        str(work),
        "--operation-id",
        _OPERATION_ID,
    ]


def test_cli_is_disabled_by_default_without_opening_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted, storage, work = _roots(tmp_path)
    monkeypatch.setattr(runner.config, "v4_rebuild_enabled", False)
    engine = MagicMock()
    monkeypatch.setattr(runner, "AsyncEngine", engine)

    exit_code = runner.main(_arguments(trusted, storage, work))

    assert exit_code == runner.EXIT_CONFIGURATION
    engine.assert_not_called()


def test_cli_refuses_to_open_storage_owned_by_api_or_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted, storage, work = _roots(tmp_path)
    monkeypatch.setattr(runner.config, "v4_rebuild_enabled", True)
    engine = MagicMock()
    monkeypatch.setattr(runner, "AsyncEngine", engine)

    with StorageWriterLock.acquire(storage, owner="combined-runtime"):
        exit_code = runner.main(_arguments(trusted, storage, work))

    assert exit_code == runner.EXIT_WRITER_ACTIVE
    engine.assert_not_called()


def test_cli_runs_claim_prepare_replay_cutover_and_releases_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    trusted, storage, work = _roots(tmp_path)
    (storage / "mesa.db").touch()
    monkeypatch.setattr(runner.config, "v4_rebuild_enabled", True)
    calls: list[str] = []
    claimed = {
        "operation_id": _OPERATION_ID,
        "state": "CLAIMED",
        "claimed_by": "runner",
        "claim_token": "claim-a",
        "fencing_token": 1,
        "progress_completed": 0,
        "progress_total": 0,
        "checkpoint": {},
    }
    verifying = {
        **claimed,
        "state": "VERIFYING",
        "progress_completed": 0,
        "progress_total": 0,
    }
    completed = {**verifying, "state": "COMPLETED"}
    preparation = RebuildPreparation(
        operation=claimed,
        generation={"generation_id": f"rebuild-{_OPERATION_ID}"},
        backup_root=work / _OPERATION_ID / "backup",
        backup_manifest_hash="a" * 64,
        source_manifest={"canonical_sha256": "b" * 64},
        source_manifest_hash="c" * 64,
        source_generation_id="legacy",
        target_generation_id=f"rebuild-{_OPERATION_ID}",
        runtime_fencing_token=0,
    )
    replay = RebuildReplayResult(
        operation=verifying,
        counts={
            "vector": 0,
            "graph_entity": 0,
            "graph_assertion": 0,
            "graph_link": 0,
        },
        completed=0,
        total=0,
    )
    cutover = RebuildCutoverResult(
        operation=completed,
        parity=MagicMock(),
        active_generation_id=f"rebuild-{_OPERATION_ID}",
        retained_generation_id="legacy",
    )

    class FakeEngine:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.close = AsyncMock(side_effect=lambda: calls.append("close"))

        async def initialize(self) -> None:
            calls.append("engine")

    operations = SimpleNamespace(
        get=AsyncMock(return_value={"operation_id": _OPERATION_ID, "state": "PENDING"}),
        claim=AsyncMock(
            side_effect=lambda *_args, **_kwargs: calls.append("claim") or claimed
        ),
    )

    class FakePreparer:
        def __init__(self, *_args: object) -> None:
            pass

        async def prepare(self, **_kwargs: object) -> RebuildPreparation:
            calls.append("prepare")
            return preparation

    class FakeReplayer:
        def __init__(self, *_args: object) -> None:
            pass

        async def replay(self, **_kwargs: object) -> RebuildReplayResult:
            calls.append("replay")
            return replay

    class FakeActivator:
        def __init__(self, *_args: object) -> None:
            pass

        async def activate(self, **_kwargs: object) -> RebuildCutoverResult:
            calls.append("cutover")
            return cutover

    monkeypatch.setattr(runner, "AsyncEngine", FakeEngine)
    monkeypatch.setattr(runner, "OperationRepository", lambda _engine: operations)
    monkeypatch.setattr(
        runner,
        "ProjectionGenerationRepository",
        lambda _engine: SimpleNamespace(),
    )
    monkeypatch.setattr(runner, "OfflineRebuildPreparer", FakePreparer)
    monkeypatch.setattr(runner, "ProjectionReplayer", FakeReplayer)
    monkeypatch.setattr(runner, "ParityGatedActivator", FakeActivator)
    monkeypatch.setattr(
        runner,
        "_provider_runtime",
        lambda: runner.RebuildProviderRuntime(
            manifest={}, embedding_provider=None, allow_model_loading=False
        ),
    )

    exit_code = runner.main(_arguments(trusted, storage, work))

    assert exit_code == runner.EXIT_OK
    assert calls == ["engine", "claim", "prepare", "replay", "cutover", "close"]
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "active_generation_id": f"rebuild-{_OPERATION_ID}",
        "operation_id": _OPERATION_ID,
        "retained_generation_id": "legacy",
        "state": "COMPLETED",
    }
    assert str(storage) not in json.dumps(output)
    with StorageWriterLock.acquire(storage, owner="post-run-check"):
        pass


def test_package_exposes_exact_rebuild_entrypoint() -> None:
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text()
    assert 'mesa-v4-rebuild = "mesa_storage.rebuild_runner:main"' in pyproject


def test_cli_turns_safe_stop_into_retryable_checkpointed_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted, storage, work = _roots(tmp_path)
    (storage / "mesa.db").touch()
    monkeypatch.setattr(runner.config, "v4_rebuild_enabled", True)
    claimed = {
        "operation_id": _OPERATION_ID,
        "state": "CLAIMED",
        "claimed_by": f"v4-rebuild-{runner.os.getpid()}",
        "claim_token": "claim-a",
        "fencing_token": 1,
        "progress_completed": 1,
        "progress_total": 2,
        "checkpoint": {"phase": "REPLAYING", "replay": {"vector": 1}},
    }
    running = {**claimed, "state": "RUNNING"}
    operations = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                {"operation_id": _OPERATION_ID, "state": "PENDING"},
                running,
            ]
        ),
        claim=AsyncMock(return_value=claimed),
        transition=AsyncMock(return_value={**running, "state": "RETRYABLE_FAILED"}),
    )
    preparation = RebuildPreparation(
        operation=running,
        generation={"generation_id": f"rebuild-{_OPERATION_ID}"},
        backup_root=work / _OPERATION_ID / "backup",
        backup_manifest_hash="a" * 64,
        source_manifest={"canonical_sha256": "b" * 64},
        source_manifest_hash="c" * 64,
        source_generation_id="legacy",
        target_generation_id=f"rebuild-{_OPERATION_ID}",
        runtime_fencing_token=0,
    )

    class FakeEngine:
        async def initialize(self) -> None:
            pass

        async def close(self) -> None:
            pass

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class FakePreparer:
        def __init__(self, *_args: object) -> None:
            pass

        async def prepare(self, **_kwargs: object) -> RebuildPreparation:
            return preparation

    class InterruptedReplayer:
        def __init__(self, *_args: object) -> None:
            pass

        async def replay(self, **kwargs: object) -> RebuildReplayResult:
            should_stop = kwargs["should_stop"]
            stop_event = should_stop.__self__  # type: ignore[attr-defined,union-attr]
            stop_event.set()
            raise RebuildInterruptedError("safe stop at batch boundary")

    monkeypatch.setattr(runner, "AsyncEngine", FakeEngine)
    monkeypatch.setattr(runner, "OperationRepository", lambda _engine: operations)
    monkeypatch.setattr(
        runner, "ProjectionGenerationRepository", lambda _engine: SimpleNamespace()
    )
    monkeypatch.setattr(runner, "OfflineRebuildPreparer", FakePreparer)
    monkeypatch.setattr(runner, "ProjectionReplayer", InterruptedReplayer)
    monkeypatch.setattr(
        runner,
        "_provider_runtime",
        lambda: runner.RebuildProviderRuntime(
            manifest={}, embedding_provider=None, allow_model_loading=False
        ),
    )

    exit_code = runner.main(_arguments(trusted, storage, work))

    assert exit_code == runner.EXIT_RETRYABLE
    operations.transition.assert_awaited_once()
    assert operations.transition.await_args.kwargs["to_state"] == "RETRYABLE_FAILED"
    assert operations.transition.await_args.kwargs["checkpoint"] == {
        "phase": "RETRYABLE_FAILED",
        "replay": {"vector": 1},
    }
    assert operations.transition.await_args.kwargs["error_class"] == (
        "RebuildInterruptedError"
    )
    with StorageWriterLock.acquire(storage, owner="post-interrupt-check"):
        pass
