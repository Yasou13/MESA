#!/usr/bin/env python3
"""Offline versioned Kùzu schema migration with staged atomic promotion."""

from __future__ import annotations

import argparse
from pathlib import Path

from mesa_storage.kuzu_schema_migration import migrate_schema_offline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kuzu-db",
        required=True,
        help="Live Kùzu artifact to migrate while all application processes are stopped.",
    )
    args = parser.parse_args()
    outcome = migrate_schema_offline(Path(args.kuzu_db))
    print(
        "KUZU_SCHEMA_MIGRATION "
        f"state={outcome.state} token={outcome.fencing_token} "
        f"live={outcome.live_path} retained_previous={outcome.backup_path}"
    )


if __name__ == "__main__":
    main()
