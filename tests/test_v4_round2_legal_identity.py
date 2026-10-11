"""Round-two regressions for provenance-backed legal identity retrieval."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config

from mesa_api.v4_router import V4LegalIdentityRequest
from mesa_memory.config import config
from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import MemoryDAO
from mesa_storage.legal_identity import canonicalize_trusted_legal_identity
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_workers.projection_worker import _triplets


def _alembic_config(database: Path) -> Config:
    config = Config(str(Path(__file__).parents[1] / "mesa_storage" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database}")
    return config


def _make_dao(engine: AsyncEngine) -> MemoryDAO:
    vector = SimpleNamespace(
        compute_embedding=AsyncMock(return_value=[1.0, 0.0]),
        compute_query_embedding=AsyncMock(return_value=[1.0, 0.0]),
        upsert=AsyncMock(),
        search=AsyncMock(return_value=[]),
    )
    graph = SimpleNamespace(
        insert_node=AsyncMock(),
        insert_assertion=AsyncMock(),
        link_assertions=AsyncMock(),
        search_v4_graph=AsyncMock(return_value=[]),
        is_operational=True,
    )
    return MemoryDAO(engine, vector, graph)


async def _record_candidate(
    dao: MemoryDAO,
    *,
    raw_log_id: int,
    document_id: str,
    content: str,
    metadata: dict,
) -> dict:
    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=raw_log_id,
        tenant_id="tenant-round2",
        workspace_id="workspace-round2",
        dataset_id="dataset-round2",
        document_id=document_id,
        revision_id=f"{document_id}:revision:1",
        chunk_id=f"{document_id}:chunk:1",
        source_ref=f"https://example.invalid/legal/{document_id}",
        agent_id="agent-round2",
        session_id="session-round2",
        content_payload=content,
        evidence_span=content,
        metadata=metadata,
        embedding_provider="test",
        embedding_model="round2",
        embedding_version="v1",
        embedding_dimension=2,
        embedding_space_id="test:round2:v1:2:norm=true",
        embedding_normalized=True,
    ).as_consolidation_record()
    await dao.record_mutation(candidate, raw_log_id=raw_log_id)
    mutation = await dao.get_projection_mutation(candidate["mutation_id"])
    assert mutation is not None
    return mutation


async def _commit(dao: MemoryDAO, mutation: dict, triplet: dict) -> dict:
    await dao.project_v4_graph_triplet(mutation=mutation, triplet=triplet)
    assertions = await dao.list_v4_assertions_for_mutation(mutation["mutation_id"])
    assert len(assertions) == 1
    async with dao._sql.transaction() as db:
        await db.execute(
            "UPDATE memory_mutations SET state = 'COMMITTED' WHERE mutation_id = ?",
            (mutation["mutation_id"],),
        )
        await db.commit()
    return assertions[0]


@pytest.mark.asyncio
async def test_fallback_preserves_trusted_parent_law_identity(tmp_path) -> None:
    """A bare article chunk must retain trusted parent law/version metadata."""
    engine = AsyncEngine(str(tmp_path / "round2-identity.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = _make_dao(engine)
    try:
        mutation = await _record_candidate(
            dao,
            raw_log_id=1,
            document_id="opaque-document-1",
            content="MADDE 1 — Sözleşme, tarafların iradelerini açıklamalarıyla kurulur.",
            metadata={
                "_mesa_legal_identity": {
                    "law_id": "6098",
                    "statute_code": "TBK",
                    "article_id": "1",
                    "version_id": "2026-current",
                    "paragraph_id": "1",
                    "clause_id": "a",
                    "identity_source": "catalog",
                },
                "jurisdiction": "TR",
                "authority_level": "PRIMARY",
            },
        )
        assertion = await _commit(dao, mutation, _triplets(mutation)[0])

        assert assertion["law_id"] == "6098"
        assert assertion["statute_code"] == "TBK"
        assert assertion["article_id"] == "1"
        assert assertion["legal_version_id"] == "2026-current"
        assert assertion["paragraph_id"] == "1"
        assert assertion["clause_id"] == "a"
        assert assertion["legal_identity_source"] == "catalog"
    finally:
        await engine.close()


def test_legal_identity_aliases_are_canonical_and_ambiguity_fails_closed() -> None:
    assert canonicalize_trusted_legal_identity(
        {
            "law_id": "Türk Borçlar Kanunu",
            "statute_code": "6098",
            "article_id": "madde-1",
            "identity_source": "verified_ingestion",
        }
    ) == {
        "law_id": "6098",
        "statute_code": "TBK",
        "article_id": "1",
        "version_id": "",
        "paragraph_id": "",
        "clause_id": "",
        "identity_source": "verified_ingestion",
    }

    for identity in (
        {"law_id": "UNKNOWN", "article_id": "1"},
        {"law_id": "6098", "statute_code": "TCK", "article_id": "1"},
        {"law_id": "6098", "article_id": "not-an-article"},
        {
            "law_id": "6098",
            "article_id": "1",
            "identity_source": "caller_metadata",
        },
    ):
        with pytest.raises(ValueError):
            canonicalize_trusted_legal_identity(identity)


def test_v4_api_legal_identity_is_typed_and_strict() -> None:
    accepted = V4LegalIdentityRequest(
        law_id="6098",
        statute_code="TBK",
        article_id="1",
        version_id="2026-current",
    )
    assert accepted.article_id == "1"

    with pytest.raises(ValueError):
        V4LegalIdentityRequest(law_id="UNKNOWN", article_id="1")
    with pytest.raises(ValueError):
        V4LegalIdentityRequest(law_id="6098", statute_code="TCK", article_id="1")


@pytest.mark.asyncio
async def test_admission_replaces_spoofed_reserved_legal_metadata(tmp_path) -> None:
    engine = AsyncEngine(str(tmp_path / "round2-admission-boundary.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = _make_dao(engine)
    try:
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-round2",
            workspace_id="workspace-round2",
            dataset_id="dataset-round2",
        )
        await dao.admit_v4_memory(
            tenant_id="tenant-round2",
            workspace_id="workspace-round2",
            dataset_id="dataset-round2",
            agent_id="agent-round2",
            session_id="session-round2",
            document_id="document-round2",
            revision_id="revision-round2",
            chunk_id="chunk-round2",
            title="Round 2",
            content_payload="MADDE 1 — doğrulanmış hüküm.",
            source_ref="https://example.invalid/legal/round2",
            evidence_span="MADDE 1 — doğrulanmış hüküm.",
            revision_number=1,
            chunk_ordinal=0,
            supersedes_revision_id=None,
            metadata={
                "_mesa_legal_identity": {
                    "law_id": "5237",
                    "statute_code": "TCK",
                    "article_id": "1",
                    "identity_source": "catalog",
                }
            },
            legal_identity={
                "law_id": "6098",
                "statute_code": "TBK",
                "article_id": "1",
            },
            embedding_provider="test",
            embedding_model="round2",
            embedding_version="v1",
            embedding_dimension=2,
            validation_mode=0,
            policy=config.queue_admission_policy,
        )
        async with engine.connection() as db:
            async with db.execute(
                "SELECT metadata_json FROM memory_mutations"
            ) as cursor:
                row = await cursor.fetchone()
        assert row is not None
        stored = json.loads(str(row["metadata_json"]))
        assert stored["_mesa_legal_identity"] == {
            "law_id": "6098",
            "statute_code": "TBK",
            "article_id": "1",
            "version_id": "",
            "paragraph_id": "",
            "clause_id": "",
            "identity_source": "verified_ingestion",
        }
    finally:
        await engine.close()


def test_existing_schema_upgrades_add_legal_identity_without_losing_fts(
    tmp_path,
) -> None:
    database = tmp_path / "existing-c4.sqlite"
    config = _alembic_config(database)
    command.upgrade(config, "c4d5e6f7a8b9")
    with sqlite3.connect(database) as connection:
        old_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(v4_assertions)")
        }
    assert "law_id" not in old_columns

    config.attributes["mesa_require_head_postflight"] = True
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(v4_assertions)")
        }
        objects = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('index', 'trigger')"
            )
        }
    assert {
        "law_id",
        "statute_code",
        "article_id",
        "paragraph_id",
        "clause_id",
        "legal_version_id",
        "legal_identity_source",
    } <= columns
    assert "idx_v4_assertions_legal_identity_scope" in objects
    assert {
        "trg_v4_assertions_fts_insert",
        "trg_v4_assertions_fts_delete",
        "trg_v4_assertions_fts_update",
    } <= objects


@pytest.mark.asyncio
async def test_same_article_wrong_law_and_wrong_article_do_not_receive_exact_boost(
    tmp_path,
) -> None:
    engine = AsyncEngine(str(tmp_path / "round2-negative-identities.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = _make_dao(engine)
    try:
        seeded: dict[str, dict] = {}
        cases = (
            (20, "tbk-1", "TBK", "6098", "1"),
            (21, "tbk-2", "TBK", "6098", "2"),
            (22, "tck-1", "TCK", "5237", "1"),
        )
        for raw_log_id, name, code, law_id, article_id in cases:
            mutation = await _record_candidate(
                dao,
                raw_log_id=raw_log_id,
                document_id=f"opaque-{name}",
                content=f"MADDE {article_id} — {name} için bağımsız düzenleme.",
                metadata={
                    "_mesa_legal_identity": {
                        "law_id": law_id,
                        "statute_code": code,
                        "article_id": article_id,
                        "version_id": "current",
                        "identity_source": "catalog",
                    },
                    "jurisdiction": "TR",
                },
            )
            seeded[name] = await _commit(dao, mutation, _triplets(mutation)[0])

        results = await dao.search_v4_memory(
            tenant_id="tenant-round2",
            agent_id="agent-round2",
            dataset_ids=["dataset-round2"],
            query="TBK madde 1",
            jurisdiction="TR",
            limit=5,
            graph_enabled=False,
        )
        by_id = {row["assertion_id"]: row for row in results}
        assert results[0]["assertion_id"] == seeded["tbk-1"]["assertion_id"]
        assert by_id[seeded["tbk-1"]["assertion_id"]]["legal_factor"] == 1.5
        assert by_id[seeded["tbk-2"]["assertion_id"]]["legal_factor"] == 1.25
        competing = by_id.get(seeded["tck-1"]["assertion_id"])
        assert competing is None or competing["legal_factor"] == 0.25
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_trusted_legal_identity_is_used_before_rank(tmp_path) -> None:
    """A heading-only distractor cannot beat the verified article chunk."""
    engine = AsyncEngine(str(tmp_path / "round2-rank.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = _make_dao(engine)
    try:
        correct_mutation = await _record_candidate(
            dao,
            raw_log_id=10,
            document_id="opaque-correct-document",
            content="MADDE 1 — Sözleşme, tarafların iradelerini açıklamalarıyla kurulur.",
            metadata={
                "_mesa_legal_identity": {
                    "law_id": "6098",
                    "statute_code": "TBK",
                    "article_id": "1",
                    "version_id": "2026-current",
                    "identity_source": "catalog",
                },
                "jurisdiction": "TR",
                "authority_level": "PRIMARY",
            },
        )
        correct = await _commit(dao, correct_mutation, _triplets(correct_mutation)[0])

        distractor_mutation = await _record_candidate(
            dao,
            raw_log_id=11,
            document_id="opaque-heading-document",
            content="6098 sayılı Türk Borçlar Kanunu genel başlığı ve içindekiler.",
            metadata={"jurisdiction": "TR", "authority_level": "SECONDARY"},
        )
        await _commit(
            dao,
            distractor_mutation,
            {
                "head": "Türk Borçlar Kanunu",
                "relation": "başlık",
                "literal_value": "Genel hükümler",
                "evidence_span": distractor_mutation["content_payload"],
            },
        )

        results = await dao.search_v4_memory(
            tenant_id="tenant-round2",
            agent_id="agent-round2",
            dataset_ids=["dataset-round2"],
            query="6098 sayılı Türk Borçlar Kanunu madde 1",
            jurisdiction="TR",
            limit=1,
            graph_enabled=False,
        )

        assert [row["assertion_id"] for row in results] == [correct["assertion_id"]]
        assert results[0]["legal_factor"] == 1.5
        assert results[0]["retrieval_provenance"]["legal_identity"] == {
            "law_id": "6098",
            "statute_code": "TBK",
            "article_id": "1",
            "version_id": "2026-current",
            "identity_source": "catalog",
        }
    finally:
        await engine.close()
