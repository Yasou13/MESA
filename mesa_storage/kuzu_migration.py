"""Fail-closed filesystem coordinator for offline Kùzu migrations.

Kùzu is an embedded, filesystem-backed database. A migration must therefore
never mutate the live artifact in place: it is built and validated at a
versioned sibling path, then promoted with same-filesystem renames while
a process-level Linux lock and SQLite journal fence concurrent writers.
"""

from __future__ import annotations

import fcntl
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, cast


class KuzuMigrationError(RuntimeError):
    """Base error for a Kùzu migration coordinator failure."""


class MigrationLockedError(KuzuMigrationError):
    """Another process owns the migration lock."""


class MigrationResumeRequired(KuzuMigrationError):
    """An interrupted but intact staging directory needs a retry."""


class MigrationStateError(KuzuMigrationError):
    """The journal and filesystem are not safe to promote automatically."""


@dataclass(frozen=True)
class MigrationOutcome:
    migration_id: str
    state: str
    fencing_token: int
    live_path: Path
    staging_path: Path | None
    backup_path: Path | None


BuildStaging = Callable[[Path], None]
ValidateStaging = Callable[[Path], None]


class KuzuMigrationCoordinator:
    """Coordinate one offline Kùzu directory migration.

    The caller supplies a build and validation function.  This keeps Kùzu DDL
    and import details out of the safety boundary and lets component tests use
    marker directories rather than a production graph store.
    """

    def __init__(self, live_path: Path | str, journal_path: Path | str) -> None:
        self.live_path = Path(live_path).resolve()
        self.journal_path = Path(journal_path).resolve()
        if self.journal_path.parent != self.live_path.parent:
            raise MigrationStateError(
                "Kùzu migration journal must be a sibling of the live artifact "
                "so staging, backup, and promotion share one filesystem."
            )
        self.lock_path = (
            self.live_path.parent / f".{self.live_path.name}.migration.lock"
        )

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Acquire a non-blocking process lock for exactly one coordinator run."""
        self.live_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise MigrationLockedError(
                    f"Kùzu migration lock is held: {self.lock_path}"
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def run(
        self,
        *,
        migration_id: str,
        source_fingerprint: str,
        target_version: str,
        build_staging: BuildStaging,
        validate_staging: ValidateStaging,
    ) -> MigrationOutcome:
        """Build, validate, and atomically promote one versioned migration."""
        if not migration_id or not source_fingerprint or not target_version:
            raise ValueError(
                "migration_id, source_fingerprint, and target_version are required"
            )
        with self.locked():
            connection = self._open_journal()
            try:
                record = self._record(connection, migration_id)
                if record is not None:
                    self._assert_matching_record(
                        record, source_fingerprint, target_version
                    )
                    recovered = self._recover_interrupted_swap(connection, record)
                    if recovered is not None:
                        return recovered
                    if record["state"] == "PROMOTED":
                        if not self.live_path.exists():
                            raise MigrationStateError(
                                "journal says PROMOTED but live Kùzu artifact is missing"
                            )
                        return self._outcome(record)
                    if record["state"] == "FAILED":
                        raise MigrationStateError(
                            "previous migration failed; inspect and resolve its staging "
                            "directory before retrying"
                        )
                    staging_path = Path(record["staging_path"])
                    fencing_token = int(record["fencing_token"])
                    if record["state"] != "STAGED" or not staging_path.exists():
                        raise MigrationStateError(
                            "journal has no resumable staged Kùzu artifact"
                        )
                else:
                    fencing_token = self._next_fencing_token(connection)
                    staging_path = self.live_path.parent / (
                        f".{self.live_path.name}.staging-{migration_id}-{fencing_token}"
                    )
                    if staging_path.exists():
                        raise MigrationStateError(
                            f"refusing to reuse unexpected staging directory: {staging_path}"
                        )
                    self._write_record(
                        connection,
                        migration_id=migration_id,
                        source_fingerprint=source_fingerprint,
                        target_version=target_version,
                        state="BUILDING",
                        fencing_token=fencing_token,
                        staging_path=staging_path,
                        backup_path=None,
                        error=None,
                    )
                    try:
                        build_staging(staging_path)
                    except MigrationResumeRequired as exc:
                        if staging_path.exists():
                            self._set_state(
                                connection, migration_id, "STAGED", str(exc)
                            )
                        else:
                            self._set_state(
                                connection, migration_id, "FAILED", str(exc)
                            )
                        raise
                    except Exception as exc:
                        self._set_state(connection, migration_id, "FAILED", str(exc))
                        raise
                    if not staging_path.exists():
                        self._set_state(
                            connection,
                            migration_id,
                            "FAILED",
                            "builder did not create a staging artifact",
                        )
                        raise MigrationStateError(
                            "builder did not create a staging artifact"
                        )
                    self._set_state(connection, migration_id, "STAGED", None)

                try:
                    validate_staging(staging_path)
                except MigrationResumeRequired as exc:
                    self._set_state(connection, migration_id, "STAGED", str(exc))
                    raise
                except Exception as exc:
                    self._set_state(connection, migration_id, "FAILED", str(exc))
                    raise

                backup_path = self.live_path.parent / (
                    f".{self.live_path.name}.rollback-{migration_id}-{fencing_token}"
                )
                self._write_record(
                    connection,
                    migration_id=migration_id,
                    source_fingerprint=source_fingerprint,
                    target_version=target_version,
                    state="VALIDATED",
                    fencing_token=fencing_token,
                    staging_path=staging_path,
                    backup_path=backup_path,
                    error=None,
                )
                return self._promote(connection, migration_id)
            finally:
                connection.close()

    def rollback(self, migration_id: str) -> MigrationOutcome:
        """Restore the retained pre-promotion directory without deleting data."""
        with self.locked():
            connection = self._open_journal()
            try:
                record = self._record(connection, migration_id)
                if record is None or record["state"] != "PROMOTED":
                    raise MigrationStateError(
                        "only a PROMOTED migration can be rolled back"
                    )
                backup_path = Path(record["backup_path"])
                if not backup_path.exists() or not self.live_path.exists():
                    raise MigrationStateError(
                        "rollback requires both live and retained backup"
                    )
                displaced_path = self.live_path.parent / (
                    f".{self.live_path.name}.rolled-forward-{migration_id}-"
                    f"{record['fencing_token']}"
                )
                if displaced_path.exists():
                    raise MigrationStateError(
                        f"refusing to overwrite retained promoted directory: {displaced_path}"
                    )
                self._set_state(connection, migration_id, "ROLLING_BACK", None)
                os.replace(self.live_path, displaced_path)
                os.replace(backup_path, self.live_path)
                self._write_record(
                    connection,
                    migration_id=migration_id,
                    source_fingerprint=record["source_fingerprint"],
                    target_version=record["target_version"],
                    state="ROLLED_BACK",
                    fencing_token=int(record["fencing_token"]),
                    staging_path=None,
                    backup_path=displaced_path,
                    error=None,
                )
                return self._outcome(self._record(connection, migration_id))
            finally:
                connection.close()

    def is_promoted(self, migration_id: str, target_version: str) -> bool:
        """Return whether this live artifact has a matching promoted journal row."""
        with self.locked():
            connection = self._open_journal()
            try:
                record = self._record(connection, migration_id)
                return bool(
                    record is not None
                    and record["state"] == "PROMOTED"
                    and record["target_version"] == target_version
                    and self.live_path.exists()
                )
            finally:
                connection.close()

    def promoted_outcome(
        self, migration_id: str, target_version: str
    ) -> MigrationOutcome:
        """Return a matching promoted record or fail closed."""
        with self.locked():
            connection = self._open_journal()
            try:
                record = self._record(connection, migration_id)
                if (
                    record is None
                    or record["state"] != "PROMOTED"
                    or record["target_version"] != target_version
                    or not self.live_path.exists()
                ):
                    raise MigrationStateError("no matching promoted Kùzu migration")
                return self._outcome(record)
            finally:
                connection.close()

    def _open_journal(self) -> sqlite3.Connection:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.journal_path)
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE IF NOT EXISTS kuzu_migration_journal ("
            "migration_id TEXT PRIMARY KEY, source_fingerprint TEXT NOT NULL, "
            "target_version TEXT NOT NULL, state TEXT NOT NULL, "
            "fencing_token INTEGER NOT NULL, staging_path TEXT, backup_path TEXT, "
            "last_error TEXT, updated_at TEXT NOT NULL)"
        )
        connection.commit()
        return connection

    @staticmethod
    def _record(
        connection: sqlite3.Connection, migration_id: str
    ) -> sqlite3.Row | None:
        row = connection.execute(
            "SELECT * FROM kuzu_migration_journal WHERE migration_id = ?",
            (migration_id,),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    @staticmethod
    def _next_fencing_token(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT COALESCE(MAX(fencing_token), 0) + 1 FROM kuzu_migration_journal"
        ).fetchone()
        return int(row[0])

    def _write_record(
        self,
        connection: sqlite3.Connection,
        *,
        migration_id: str,
        source_fingerprint: str,
        target_version: str,
        state: str,
        fencing_token: int,
        staging_path: Path | None,
        backup_path: Path | None,
        error: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO kuzu_migration_journal "
            "(migration_id, source_fingerprint, target_version, state, fencing_token, "
            "staging_path, backup_path, last_error, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(migration_id) DO UPDATE SET "
            "source_fingerprint=excluded.source_fingerprint, "
            "target_version=excluded.target_version, state=excluded.state, "
            "fencing_token=excluded.fencing_token, staging_path=excluded.staging_path, "
            "backup_path=excluded.backup_path, last_error=excluded.last_error, "
            "updated_at=excluded.updated_at",
            (
                migration_id,
                source_fingerprint,
                target_version,
                state,
                fencing_token,
                None if staging_path is None else str(staging_path),
                None if backup_path is None else str(backup_path),
                error,
                now,
            ),
        )
        connection.commit()

    def _set_state(
        self,
        connection: sqlite3.Connection,
        migration_id: str,
        state: str,
        error: str | None,
    ) -> None:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE kuzu_migration_journal SET state = ?, last_error = ?, updated_at = ? "
            "WHERE migration_id = ?",
            (state, error, datetime.now(timezone.utc).isoformat(), migration_id),
        )
        connection.commit()

    def _promote(
        self, connection: sqlite3.Connection, migration_id: str
    ) -> MigrationOutcome:
        record = self._record(connection, migration_id)
        assert record is not None
        staging_path = Path(record["staging_path"])
        backup_path = Path(record["backup_path"])
        if not staging_path.exists():
            raise MigrationStateError("validated staging artifact disappeared")
        if backup_path.exists():
            raise MigrationStateError(
                f"refusing to overwrite retained rollback directory: {backup_path}"
            )
        self._set_state(connection, migration_id, "SWAPPING", None)
        if self.live_path.exists():
            os.replace(self.live_path, backup_path)
        try:
            os.replace(staging_path, self.live_path)
        except Exception as exc:
            self._set_state(
                connection, migration_id, "SWAP_RECOVERY_REQUIRED", str(exc)
            )
            raise
        self._set_state(connection, migration_id, "PROMOTED", None)
        promoted = self._record(connection, migration_id)
        assert promoted is not None
        return self._outcome(promoted)

    def _recover_interrupted_swap(
        self, connection: sqlite3.Connection, record: sqlite3.Row
    ) -> MigrationOutcome | None:
        if record["state"] != "SWAPPING":
            return None
        staging_path = Path(record["staging_path"])
        backup_path = Path(record["backup_path"])
        if (
            self.live_path.exists()
            and not staging_path.exists()
            and backup_path.exists()
        ):
            self._set_state(connection, record["migration_id"], "PROMOTED", None)
        elif (
            not self.live_path.exists()
            and staging_path.exists()
            and backup_path.exists()
        ):
            os.replace(staging_path, self.live_path)
            self._set_state(connection, record["migration_id"], "PROMOTED", None)
        else:
            raise MigrationStateError(
                "interrupted Kùzu swap is ambiguous; operator inspection is required"
            )
        recovered = self._record(connection, record["migration_id"])
        assert recovered is not None
        return self._outcome(recovered)

    @staticmethod
    def _assert_matching_record(
        record: sqlite3.Row, source_fingerprint: str, target_version: str
    ) -> None:
        if (
            record["source_fingerprint"] != source_fingerprint
            or record["target_version"] != target_version
        ):
            raise MigrationStateError(
                "migration_id is already bound to a different source fingerprint or "
                "target version"
            )

    def _outcome(self, record: sqlite3.Row | None) -> MigrationOutcome:
        if record is None:
            raise MigrationStateError("migration journal record disappeared")
        return MigrationOutcome(
            migration_id=str(record["migration_id"]),
            state=str(record["state"]),
            fencing_token=int(record["fencing_token"]),
            live_path=self.live_path,
            staging_path=(
                None if record["staging_path"] is None else Path(record["staging_path"])
            ),
            backup_path=(
                None if record["backup_path"] is None else Path(record["backup_path"])
            ),
        )
