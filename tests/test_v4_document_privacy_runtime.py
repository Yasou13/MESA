"""Document lifecycle privacy through the combined runtime and HTTP API."""

import argparse
import hashlib
import json
import sqlite3

import pytest

from mesa_memory.api import server
from tests.test_model_optional_runtime import _consume
from tests.test_model_optional_runtime import model_runtime as model_runtime


async def _grant_lifecycle_permissions():
    access = server.state.access_control
    await access.grant_scope_role(
        "model-principal",
        tenant_id="model-tenant",
        workspace_id="default",
        dataset_id="main",
        role="OWNER",
    )
    for permission in ("PURGE", "ROLLBACK"):
        await access.grant_dataset_permission(
            "model-principal",
            tenant_id="model-tenant",
            dataset_id="main",
            permission=permission,
        )


async def _insert_document(env, document="privacy", revision=1, content=None, **fields):
    payload = {
        "session_id": env.session_id,
        "dataset_id": "main",
        "document_id": document,
        "revision_id": f"{document}-r{revision}",
        "revision_number": revision,
        "chunk_id": f"{document}-c{revision}",
        "title": "Privacy lifecycle",
        "source_ref": "privacy-test",
        "content": content or f"PRIVATE_REVISION_{revision} durable memory.",
    }
    if revision > 1:
        payload["supersedes_revision_id"] = f"{document}-r{revision - 1}"
    payload.update(fields)
    response = await env.client.post("/v4/memory/insert", json=payload)
    assert response.status_code == 202, response.text
    return response.json()["mutation_id"]


async def _context(env):
    response = await env.client.get(f"/v4/sessions/{env.session_id}/context")
    assert response.status_code == 200, response.text
    return response.json()["context"]


@pytest.mark.asyncio
@pytest.mark.parametrize("embedding", [False, True])
@pytest.mark.parametrize("action", ["purge", "rollback", "supersede"])
async def test_session_context_drops_removed_revision(model_runtime, action, embedding):
    async with model_runtime(MESA_EMBEDDING_ENABLED=embedding) as env:
        await _grant_lifecycle_permissions()
        mutation_id = await _insert_document(env)
        # Pending evidence must remain available in the originating session.
        assert "PRIVATE_REVISION_1" in await _context(env)
        await _consume(env, 3)
        assert (await env.dao.get_mutation("model-agent", mutation_id))[
            "state"
        ] == "COMMITTED"
        assert "PRIVATE_REVISION_1" in await _context(env)
        if action == "purge":
            response = await env.client.delete(
                "/v4/catalog/documents/privacy",
                params={
                    "tenant_id": "model-tenant",
                    "workspace_id": "default",
                    "dataset_id": "main",
                },
            )
            assert response.status_code == 202, response.text
        elif action == "rollback":
            response = await env.client.post(f"/v4/mutations/{mutation_id}/rollback")
            assert response.status_code == 202, response.text
        else:
            await _insert_document(env, revision=2)
        await _consume(env, 12)
        context = await _context(env)
        assert "PRIVATE_REVISION_1" not in context
        if action == "supersede":
            assert "PRIVATE_REVISION_2" in context
        await _insert_document(
            env, document="pending", content="PENDING_TEXT stays visible."
        )
        assert "PENDING_TEXT" in await _context(env)
        await env.dao.insert_raw_log(
            "model-agent",
            {"session_id": env.session_id, "content": "UNLINKED_TEXT stays visible."},
        )
        assert "UNLINKED_TEXT" in await _context(env)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["REJECTED", "CANCELLED"])
async def test_session_context_drops_terminal_mutation(model_runtime, state):
    async with model_runtime() as env:
        mutation_id = await _insert_document(env)
        assert "PRIVATE_REVISION_1" in await _context(env)
        if state == "CANCELLED":
            async with env.dao._sql.transaction() as db:
                assert await env.dao._transition_memory_mutation_in_tx(
                    db, mutation_id, to_state=state
                )
                await db.commit()
        else:
            assert await env.dao.set_mutation_state("model-agent", mutation_id, state)
        assert "PRIVATE_REVISION_1" not in await _context(env)


def _sqlite_text_hits(database, marker):
    """Inspect every live SQLite text value, including FTS shadow tables."""
    hits = {}
    with sqlite3.connect(database) as db:
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        for (table,) in tables:
            quoted_table = '"' + table.replace('"', '""') + '"'
            for column in db.execute(f"PRAGMA table_info({quoted_table})").fetchall():
                name = column[1]
                quoted_column = '"' + name.replace('"', '""') + '"'
                count = db.execute(
                    f"SELECT count(*) FROM {quoted_table} "
                    f"WHERE typeof({quoted_column}) = 'text' AND instr({quoted_column}, ?) > 0",
                    (marker,),
                ).fetchone()[0]
                if count:
                    hits[f"{table}.{name}"] = count
    return hits


def _vector_rows(dao):
    return [
        row
        for table in dao._vec._list_table_names()
        for row in dao._vec._db.open_table(table).to_arrow().to_pylist()
    ]


def _vector_text_hits(dao, marker):
    return [row for row in _vector_rows(dao) if marker in json.dumps(row)]


async def _purge_document(env, document):
    response = await env.client.delete(
        f"/v4/catalog/documents/{document}",
        params={
            "tenant_id": "model-tenant",
            "workspace_id": "default",
            "dataset_id": "main",
        },
    )
    assert response.status_code == 202, response.text
    await _consume(env, 20)
    async with env.dao._sql.connection() as db:
        assert (
            await (
                await db.execute(
                    "SELECT count(*) FROM artifact_cleanup_outbox WHERE state != 'COMPLETED'"
                )
            ).fetchone()
        )[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("extraction", [False, True])
async def test_purge_erases_text_and_preserves_other_documents(
    model_runtime, tmp_path, monkeypatch, extraction
):
    from mesa_memory import rebuild_runner
    from mesa_storage.repositories.operations import OperationRepository
    from mesa_storage.sqlite_engine import AsyncEngine
    from tests.test_d008_model_enabled_runtime_e2e import _embedding_service

    marker = "ERASEUNIQUEPRIVACYMARKER"
    shared = "KEEPSHAREDPRIVACYMARKER durable memory."
    async with model_runtime(
        MESA_EMBEDDING_ENABLED=True,
        MESA_EXTRACTION_ENABLED=extraction,
        MESA_V4_REBUILD_ENABLED=True,
    ) as env:
        await _grant_lifecycle_permissions()
        erased = await _insert_document(
            env,
            content=f"{marker} durable memory.",
            evidence_span=marker,
            title=f"Private {marker}",
            metadata={"source_note": marker},
            legal_identity={
                "law_id": "tbk",
                "statute_code": "TBK",
                "article_id": "344",
            },
        )
        removed_twin = await _insert_document(
            env, document="removed-twin", content=shared
        )
        survivor = await _insert_document(env, document="survivor", content=shared)
        await _consume(env, 6)
        for mutation_id in (erased, removed_twin, survivor):
            assert (await env.dao.get_mutation("model-agent", mutation_id))[
                "state"
            ] == "COMMITTED"
        original_survivor = await env.dao.get_mutation("model-agent", survivor)
        storage = server.state.runtime_profile.storage_root
        database = storage / "mesa.db"
        assert _sqlite_text_hits(database, marker)
        erased_assertions = await env.dao.list_v4_assertions_for_mutation(erased)
        erased_vector_ids = {row["assertion_id"] for row in erased_assertions}
        assert erased_vector_ids <= {row["node_id"] for row in _vector_rows(env.dao)}
        await _purge_document(env, "privacy")
        assert _sqlite_text_hits(database, marker) == {}
        assert _vector_text_hits(env.dao, marker) == []
        assert not erased_vector_ids & {row["node_id"] for row in _vector_rows(env.dao)}
        purged = await env.dao.get_mutation("model-agent", erased)
        assert purged["state"] == "PURGED"
        assert purged["mutation_id"] == erased
        async with env.dao._sql.connection() as db:
            chunk = await (
                await db.execute(
                    "SELECT c.content_hash FROM source_chunks c JOIN memory_mutations m "
                    "ON m.chunk_id = c.chunk_id WHERE m.mutation_id = ?",
                    (erased,),
                )
            ).fetchone()
        assert (
            chunk[0] == hashlib.sha256(f"{marker} durable memory.".encode()).hexdigest()
        )
        await _purge_document(env, "removed-twin")
        assert await env.dao.get_mutation("model-agent", survivor) == original_survivor
        assert shared in await _context(env)
        await env.restart()
        assert _sqlite_text_hits(database, marker) == {}
        assert _vector_text_hits(env.dao, marker) == []
        assert shared in await _context(env)
        await env.stop()
        sql = AsyncEngine(str(database))
        await sql.initialize()
        try:
            submitted = await OperationRepository(sql).submit(
                requested_by_principal_id="model-principal",
                idempotency_key="privacy-rebuild",
                payload_hash=hashlib.sha256(b"privacy-rebuild").hexdigest(),
            )
        finally:
            await sql.close()
        work_root = tmp_path / "rebuild-work"
        work_root.mkdir()
        monkeypatch.setattr(
            rebuild_runner,
            "get_embedding_service",
            lambda **kw: _embedding_service(env.provider),
        )
        result = await rebuild_runner.run_rebuild(
            argparse.Namespace(
                trusted_root=tmp_path,
                storage_root=storage,
                work_root=work_root,
                operation_id=str(submitted["operation_id"]),
                batch_size=100,
                lease_seconds=300,
            )
        )
        assert result == rebuild_runner.EXIT_OK
        await env.restart()
        assert _sqlite_text_hits(database, marker) == {}
        assert _vector_text_hits(env.dao, marker) == []
        assert shared in await _context(env)

        search = await env.client.post(
            "/v4/memory/search",
            json={"session_id": env.session_id, "query": shared},
        )
        assert search.status_code == 200, search.text
        assert any(
            provenance["document_id"] == "survivor"
            for result in search.json()["results"]
            for provenance in result["provenance"]
        )


@pytest.mark.asyncio
async def test_purge_preserves_assertion_owned_by_another_document(model_runtime):
    async with model_runtime(
        MESA_EMBEDDING_ENABLED=True, MESA_EXTRACTION_ENABLED=True
    ) as env:
        await _grant_lifecycle_permissions()
        original = await _insert_document(
            env, content="SHAREDASSERTIONMARKER durable memory."
        )
        survivor = await _insert_document(
            env,
            document="survivor",
            content="Another document still uses this assertion.",
        )
        await _consume(env, 6)
        assertions = await env.dao.list_v4_assertions_for_mutation(original)
        assert len(assertions) == 1
        assertion_id = assertions[0]["assertion_id"]
        # The ownership ledger supports reuse of the same physical assertion.
        async with env.dao._sql.connection() as db:
            original_artifacts = [
                dict(row)
                for row in await (
                    await db.execute(
                        "SELECT * FROM memory_artifacts WHERE mutation_id = ? AND artifact_id = ?",
                        (original, assertion_id),
                    )
                ).fetchall()
            ]
        for artifact in original_artifacts:
            await env.dao.record_mutation_artifact(
                survivor,
                store_name=artifact["store_name"],
                artifact_kind=artifact["artifact_kind"],
                artifact_id=assertion_id,
                metadata=json.loads(artifact["metadata_json"]),
            )
        async with env.dao._sql.connection() as db:
            before = dict(
                await (
                    await db.execute(
                        "SELECT * FROM v4_assertions WHERE assertion_id = ?",
                        (assertion_id,),
                    )
                ).fetchone()
            )
        await _purge_document(env, "privacy")
        async with env.dao._sql.connection() as db:
            after = dict(
                await (
                    await db.execute(
                        "SELECT * FROM v4_assertions WHERE assertion_id = ?",
                        (assertion_id,),
                    )
                ).fetchone()
            )
        assert after == before
        assert assertion_id in {row["node_id"] for row in _vector_rows(env.dao)}


def _raw_file_hits(root, marker):
    """Count the marker in raw file bytes (free pages, WAL, FTS segments)."""
    needles = (marker.encode(), marker.lower().encode())
    hits = {}
    for path in root.rglob("*"):
        # Kuzu keeps deleted strings in its own file; tracked for the owner.
        if not path.is_file() or path.name.startswith("kuzu_db"):
            continue
        count = sum(path.read_bytes().count(needle) for needle in needles)
        if count:
            hits[str(path.relative_to(root))] = count
    return hits


@pytest.mark.asyncio
@pytest.mark.parametrize("extraction", [False, True])
async def test_purge_leaves_no_text_in_raw_storage_files(model_runtime, extraction):
    marker = "RAWBYTESPRIVACYMARKER"
    async with model_runtime(
        MESA_EMBEDDING_ENABLED=True, MESA_EXTRACTION_ENABLED=extraction
    ) as env:
        await _grant_lifecycle_permissions()
        await _insert_document(
            env,
            content=f"{marker} durable memory.",
            evidence_span=marker,
            title=f"Private {marker}",
            metadata={"source_note": marker},
        )
        await _insert_document(env, document="survivor", content="Other durable memory.")
        await _consume(env, 6)
        storage = server.state.runtime_profile.storage_root
        assert _raw_file_hits(storage, marker)
        await _purge_document(env, "privacy")
        # mesa.db, mesa.db-wal, LanceDB files and JSONL queues while running.
        assert _raw_file_hits(storage, marker) == {}
        assert "Other durable memory." in await _context(env)
        await env.stop()
        assert _raw_file_hits(storage, marker) == {}
