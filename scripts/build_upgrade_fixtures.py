"""Build D116 SQLite fixtures using the published MESA wheels and public routes."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

VERSIONS = ("0.9.3", "0.9.4", "0.9.5")
ASSERTION_WEIGHTS = {"0.9.3": "1.0", "0.9.4": "0.0", "0.9.5": "0.0"}
DOCUMENTS = (
    ("port-permit", "Harbor permit", "The harbor permit lasts for twelve months."),
    ("port-fee", "Harbor fee", "The harbor permit fee is forty silver coins."),
    ("port-appeal", "Harbor appeal", "The harbor appeal hearing begins on Tuesday."),
)
QUERIES = ("harbor permit", "appeal hearing", "silver coins")
RANKED_FIELDS = (
    "document_id",
    "assertion_id",
    "evidence_span",
    "final_score",
)
LOCAL_HTTP = build_opener(ProxyHandler({}))


def request(base, route, *, data=None, key=None, expected=(200,)):
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["X-API-Key"] = key
    body = json.dumps(data).encode() if data is not None else None
    query = Request(base + route, data=body, headers=headers)
    try:
        response = LOCAL_HTTP.open(query, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        if response.code not in expected:
            raise RuntimeError(f"{route} returned HTTP {response.code}")
        return json.load(response)


@contextmanager
def serve(python: Path, home: Path, log: Path, *, settings=None) -> Iterator[dict]:
    home.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("MESA_", "LLM_", "OPENAI_")) and name != "PYTHONPATH"
    }
    env.update(
        MESA_MODEL_ENABLED="false",
        MESA_EXTERNAL_PROVIDER_ENABLED="false",
        MESA_LOAD_DOTENV="false",
        MESA_RERANK_ENABLED="false",
    )
    env.update(settings or {})
    cli = python.with_name("mesa.exe" if os.name == "nt" else "mesa")
    command = [
        str(cli),
        "start",
        "--home",
        str(home),
        "--port",
        str(port),
    ]
    base = f"http://127.0.0.1:{port}"
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            command, cwd=home, env=env, stdout=output, stderr=subprocess.STDOUT
        )
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"MESA exited {process.returncode}. See {log}")
            try:
                request(base, "/health/init")
                break
            except (URLError, RuntimeError):
                time.sleep(0.2)
        else:
            raise RuntimeError(f"MESA startup timed out. See {log}")
        credentials = json.loads((home / "credentials.json").read_text())
        assert credentials["base_url"] == base
        yield credentials
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def session(credentials):
    base, key = credentials["base_url"], credentials["api_key"]
    scope = {name: credentials[name] for name in ("tenant_id", "workspace_id")}
    dataset = credentials["dataset_id"]
    request(
        base, "/v4/catalog/workspaces", data=scope, key=key, expected=(200, 201, 409)
    )
    request(
        base,
        "/v4/catalog/datasets",
        data=dict(scope, dataset_id=dataset),
        key=key,
        expected=(200, 201, 409),
    )
    payload = dict(scope, dataset_ids=[dataset], agent_id=credentials["agent_id"])
    return request(base, "/v4/sessions/start", data=payload, key=key, expected=(201,))[
        "session_id"
    ]


def ranked_results(credentials, session_id):
    results = {}
    for query in QUERIES:
        payload = dict(
            query=query, session_id=session_id, limit=10, graph_mode="disabled"
        )
        response = request(
            credentials["base_url"],
            "/v4/memory/search",
            data=payload,
            key=credentials["api_key"],
        )
        hits = response["results"]
        if not hits:
            raise RuntimeError(f"No fixture hits for {query!r}")
        ranked = []
        for hit in hits:
            provenance = hit["provenance"][0]
            # 0.9.3 and 0.9.4 expose these identifiers inside provenance.
            entry = {name: hit[name] for name in RANKED_FIELDS}
            entry["revision_id"] = hit.get("revision_id") or provenance["revision_id"]
            entry["chunk_id"] = hit.get("chunk_id") or provenance["chunk_id"]
            ranked.append(entry)
        results[query] = ranked
    return results


def wait_for_ingests(database, expected):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        with closing(sqlite3.connect(database)) as db:
            states = dict(
                db.execute(
                    "SELECT state, COUNT(*) FROM memory_mutations GROUP BY state"
                )
            )
        if states.get("DEAD_LETTER", 0):
            raise RuntimeError(f"Fixture ingestion failed: {states}")
        if states.get("COMMITTED") == expected:
            return
        time.sleep(0.2)
    raise RuntimeError(f"Fixture ingestion did not settle: {states}")


def build_version(version, python, output, scratch):
    work = scratch / version
    work.mkdir(parents=True)
    env = work / "venv"
    subprocess.run(["uv", "venv", "--python", python, str(env)], check=True)
    executable = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(executable),
            "--default-index",
            "https://pypi.org/simple",
            "--only-binary",
            "mesa-memory",
            f"mesa-memory=={version}",
        ],
        check=True,
    )
    installed = subprocess.check_output(
        [
            str(executable),
            "-I",
            "-c",
            "from importlib.metadata import version; print(version('mesa-memory'))",
        ],
        cwd=work,
        text=True,
    ).strip()
    if installed != version:
        raise RuntimeError(f"Expected wheel {version}, found {installed}")
    home = work / "home"
    database = home / "data/mesa.db"
    search_settings = {"MESA_RRF_ASSERTION_WEIGHT": ASSERTION_WEIGHTS[version]}
    with serve(
        executable, home, work / "server.log", settings=search_settings
    ) as credentials:
        session_id = session(credentials)
        expected_chunks = 0
        for document, title, content in DOCUMENTS:
            payload = dict(
                session_id=session_id,
                dataset_id=credentials["dataset_id"],
                document_id=document,
                title=title,
                content=content,
                source_ref=f"fixture://{document}",
            )
            response = request(
                credentials["base_url"],
                "/v4/documents",
                data=payload,
                key=credentials["api_key"],
                expected=(202,),
            )
            expected_chunks += response["chunk_count"]
        wait_for_ingests(database, expected_chunks)
        results = ranked_results(credentials, session_id)

    snapshot = work / "snapshot.db"
    with (
        closing(sqlite3.connect(database)) as original,
        closing(sqlite3.connect(snapshot)) as copy,
    ):
        assert original.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        schema = original.execute("SELECT version_num FROM alembic_version").fetchone()[
            0
        ]
        original.backup(copy)
    raw = snapshot.read_bytes()
    destination = output / version
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "mesa.db.gz").write_bytes(gzip.compress(raw, mtime=0))
    receipt = dict(
        version=version,
        schema=schema,
        sha256=hashlib.sha256(raw).hexdigest(),
        chunks=expected_chunks,
        search_settings=search_settings,
        results=results,
    )
    (destination / "expected.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(
        f"Saved MESA {version} fixture with {expected_chunks} committed chunks",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python", default="3.11", help="Python for the historical wheels"
    )
    parser.add_argument("--output", type=Path, default=Path("tests/fixtures/upgrade"))
    parser.add_argument(
        "--scratch", type=Path, help="Keep the original stores and logs here"
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="mesa-upgrade-") as temporary:
        scratch = (args.scratch or Path(temporary)).resolve()
        for version in VERSIONS:
            try:
                build_version(version, args.python, args.output.resolve(), scratch)
            except Exception as error:
                sys.exit(f"MESA {version} fixture failed. {error}")


if __name__ == "__main__":
    main()
