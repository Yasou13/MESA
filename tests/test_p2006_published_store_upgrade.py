"""Exercise normal startup and search on stores created by the published wheels."""

import gzip
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

from scripts.build_upgrade_fixtures import VERSIONS, ranked_results, serve, session
from tests.test_migration_closure import HEAD

FIXTURES = Path(__file__).parent / "fixtures" / "upgrade"
REMOVED_SETTINGS = {"MESA_GRAPH_ENABLED": "true", "MESA_RRF_GRAPH_WEIGHT": "0.99"}


@pytest.mark.parametrize("version", VERSIONS)
def test_published_store_upgrades_with_identical_model_off_rankings(version, tmp_path):
    fixture = FIXTURES / version
    receipt = json.loads((fixture / "expected.json").read_text())
    raw = gzip.decompress((fixture / "mesa.db.gz").read_bytes())
    assert receipt["version"] == version
    assert hashlib.sha256(raw).hexdigest() == receipt["sha256"]

    home = tmp_path / "mesa"
    database = home / "data" / "mesa.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(raw)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (receipt["schema"],)

    legacy_graph = database.parent / "kuzu_db"
    legacy_graph.write_bytes(b"Legacy Kuzu storage marker")
    log = tmp_path / "upgraded.log"
    settings = {**receipt["search_settings"], **REMOVED_SETTINGS}
    with serve(Path(sys.executable), home, log, settings=settings) as credentials:
        assert ranked_results(credentials, session(credentials)) == receipt["results"]

    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (HEAD,)
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_mutations WHERE state = 'COMMITTED'"
        ).fetchone() == (receipt["chunks"],)
    assert legacy_graph.read_bytes() == b"Legacy Kuzu storage marker"
    startup = log.read_text(encoding="utf-8")
    for setting in REMOVED_SETTINGS:
        assert (
            f"Environment variable '{setting}' has been removed and is ignored."
            in startup
        )
