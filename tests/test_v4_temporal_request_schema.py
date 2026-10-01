"""Tests for V4 temporal HTTP request schema and effective-date boundary regression.

Covers:
1. Direct V4SearchRequest temporal parsing (aware datetimes, Z strings, offsets, None).
2. Direct V4SearchRequest rejection of malformed, naive, partial, and non-string types.
3. Temporal range validation (valid_from <= valid_to) and model immutability (frozen=True).
4. Strict mode preservation on unrelated fields.
5. HTTP API level parsing of the exact VM request shape (POST /v4/memory/search).
6. HTTP API rejection of malformed temporal strings with 422 VALIDATION_ERROR.
7. End-to-end effective-date boundary search regression through HTTP API to SQLite temporal filtering.
8. OpenAPI schema date-time typing preservation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from pydantic import ValidationError

from mesa_api.v4_router import V4SearchRequest, create_v4_router
from mesa_client.client import AsyncMesaV4Client
from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine

# ===========================================================================
# 1. DIRECT V4SearchRequest MODEL TESTS
# ===========================================================================


def test_v4_search_request_accepts_valid_temporal_inputs() -> None:
    """Verify that V4SearchRequest accepts timezone-aware datetimes, Z strings, and offsets."""
    # 1. Actual datetime objects (timezone-aware)
    aware_utc = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    aware_offset = datetime(2026, 6, 1, 15, 0, 0, tzinfo=timezone(timedelta(hours=3)))
    req_dt = V4SearchRequest(
        session_id="session-1",
        query="test query",
        valid_at=aware_utc,
        valid_from=aware_utc,
        valid_to=aware_offset,
    )
    assert req_dt.valid_at == aware_utc
    assert req_dt.valid_from == aware_utc
    assert req_dt.valid_to == aware_offset

    # 2. ISO-8601 strings with Z
    req_z = V4SearchRequest.model_validate(
        {
            "session_id": "session-1",
            "query": "test query",
            "valid_at": "2026-06-01T00:00:00Z",
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_to": "2026-12-31T23:59:59Z",
        }
    )
    assert req_z.valid_at == datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert req_z.valid_from == datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert req_z.valid_to == datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    assert req_z.valid_at.tzinfo is not None

    # 3. ISO-8601 strings with positive offset (+03:00)
    req_pos = V4SearchRequest.model_validate(
        {
            "session_id": "session-1",
            "query": "test query",
            "valid_at": "2026-06-01T03:00:00+03:00",
        }
    )
    assert req_pos.valid_at == datetime(
        2026, 6, 1, 3, 0, 0, tzinfo=timezone(timedelta(hours=3))
    )

    # 4. ISO-8601 strings with negative offset (-05:00)
    req_neg = V4SearchRequest.model_validate(
        {
            "session_id": "session-1",
            "query": "test query",
            "valid_at": "2026-06-01T00:00:00-05:00",
        }
    )
    assert req_neg.valid_at == datetime(
        2026, 6, 1, 0, 0, 0, tzinfo=timezone(timedelta(hours=-5))
    )

    # 5. ISO-8601 strings with fractional seconds
    req_frac_z = V4SearchRequest.model_validate(
        {
            "session_id": "session-1",
            "query": "test query",
            "valid_at": "2026-06-01T10:20:30.123456Z",
        }
    )
    assert req_frac_z.valid_at == datetime(
        2026, 6, 1, 10, 20, 30, 123456, tzinfo=timezone.utc
    )

    req_frac_offset = V4SearchRequest.model_validate(
        {
            "session_id": "session-1",
            "query": "test query",
            "valid_at": "2026-06-01T10:20:30.500+02:00",
        }
    )
    assert req_frac_offset.valid_at == datetime(
        2026, 6, 1, 10, 20, 30, 500000, tzinfo=timezone(timedelta(hours=2))
    )

    # 6. None values
    req_none = V4SearchRequest(
        session_id="session-1",
        query="test query",
        valid_at=None,
        valid_from=None,
        valid_to=None,
    )
    assert req_none.valid_at is None
    assert req_none.valid_from is None
    assert req_none.valid_to is None


@pytest.mark.parametrize(
    ("bad_field", "bad_value"),
    [
        ("valid_at", "2026-06-01T00:00:00"),  # naive timestamp (no timezone offset)
        ("valid_at", "2026-06-01"),  # date-only (no time/timezone)
        ("valid_at", "2026-06"),  # partial date
        ("valid_at", "2026"),  # year-only
        ("valid_at", "2026-13-01T00:00:00Z"),  # invalid month
        ("valid_at", "2026-02-30T00:00:00Z"),  # invalid day
        ("valid_at", "2026-06-01T25:00:00Z"),  # invalid hour
        ("valid_at", "2026-06-01T00:65:00Z"),  # invalid minute
        ("valid_at", "2026-06-01T00:00:65Z"),  # invalid second
        ("valid_at", "June 1 2026"),  # human-readable date
        ("valid_at", "01/06/2026"),  # slash format
        ("valid_at", "tomorrow"),  # relative date
        ("valid_at", "2026-06-01T00:00:00+03"),  # missing offset minutes
        ("valid_at", "2026-06-01T00:00:00+25:00"),  # out-of-range offset
        ("valid_at", "2026-06-01T00:00:00Z extra"),  # trailing junk
        ("valid_at", ""),  # empty string
        ("valid_at", "   "),  # whitespace string
        ("valid_at", 1717200000),  # unix timestamp integer
        ("valid_at", 1717200000.5),  # float timestamp
        ("valid_at", True),  # boolean
        ("valid_at", ["2026-06-01T00:00:00Z"]),  # list
        ("valid_at", {"iso": "2026-06-01T00:00:00Z"}),  # dict
        ("valid_from", "2026-06-01T00:00:00"),  # naive on valid_from
        ("valid_to", "2026-06-01T00:00:00"),  # naive on valid_to
    ],
)
def test_v4_search_request_rejects_malformed_temporal_inputs(
    bad_field: str, bad_value: Any
) -> None:
    """Verify that malformed or naive temporal inputs are strictly rejected under validation."""
    with pytest.raises(ValidationError):
        V4SearchRequest.model_validate(
            {
                "session_id": "session-1",
                "query": "test query",
                bad_field: bad_value,
            }
        )


def test_v4_search_request_temporal_range_validation() -> None:
    """Verify that valid_from <= valid_to passes and valid_from > valid_to fails."""
    # valid_from < valid_to -> PASS
    req_ok = V4SearchRequest.model_validate(
        {
            "session_id": "session-1",
            "query": "test query",
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_to": "2026-12-31T23:59:59Z",
        }
    )
    assert req_ok.valid_from is not None and req_ok.valid_to is not None
    assert req_ok.valid_from < req_ok.valid_to

    # valid_from == valid_to -> PASS
    req_eq = V4SearchRequest.model_validate(
        {
            "session_id": "session-1",
            "query": "test query",
            "valid_from": "2026-06-01T00:00:00Z",
            "valid_to": "2026-06-01T00:00:00Z",
        }
    )
    assert req_eq.valid_from is not None and req_eq.valid_to is not None
    assert req_eq.valid_from == req_eq.valid_to

    # Cross-timezone comparison: 12:00+03:00 (09:00 UTC) vs 10:00Z -> PASS
    req_tz = V4SearchRequest.model_validate(
        {
            "session_id": "session-1",
            "query": "test query",
            "valid_from": "2026-06-01T12:00:00+03:00",
            "valid_to": "2026-06-01T10:00:00Z",
        }
    )
    assert req_tz.valid_from is not None and req_tz.valid_to is not None
    assert req_tz.valid_from < req_tz.valid_to

    # valid_from > valid_to -> FAIL
    with pytest.raises(ValidationError) as exc_info:
        V4SearchRequest.model_validate(
            {
                "session_id": "session-1",
                "query": "test query",
                "valid_from": "2026-12-31T23:59:59Z",
                "valid_to": "2026-01-01T00:00:00Z",
            }
        )
    assert "valid_from must not be after valid_to" in str(exc_info.value)


def test_v4_search_request_frozen_immutability() -> None:
    """Verify that frozen=True is preserved and mutation is prohibited."""
    req = V4SearchRequest.model_validate(
        {
            "session_id": "session-1",
            "query": "test query",
            "valid_at": "2026-06-01T00:00:00Z",
        }
    )
    with pytest.raises((ValidationError, TypeError)):
        req.valid_at = datetime.now(timezone.utc)  # type: ignore[misc]


def test_v4_search_request_preserves_strict_mode_for_other_fields() -> None:
    """Verify that strict=True remains enforced for non-temporal fields."""
    # Integer as query string -> must fail under strict mode
    with pytest.raises(ValidationError):
        V4SearchRequest.model_validate(
            {
                "session_id": "session-1",
                "query": 12345,  # int instead of str
            }
        )

    # String as limit int -> must fail under strict mode
    with pytest.raises(ValidationError):
        V4SearchRequest.model_validate(
            {
                "session_id": "session-1",
                "query": "test",
                "limit": "10",  # str instead of int
            }
        )


# ===========================================================================
# 2. HTTP API VALIDATION TESTS (POST /v4/memory/search)
# ===========================================================================


def _build_test_api(
    dao: Any, principal_id: str = "principal-a", allowed: bool = True
) -> FastAPI:
    async def attach_principal(request: Request) -> None:
        request.state.principal = SimpleNamespace(
            principal_id=principal_id, principal_type="USER", status="active"
        )

    access_control = MagicMock()
    access_control.check_principal_permission = AsyncMock(return_value=allowed)
    access_control.check_principal_session_access = AsyncMock(return_value=allowed)
    access_control.check_access = AsyncMock(return_value=allowed)
    access_control.check_scope_role = AsyncMock(return_value=allowed)
    access_control.check_dataset_permission = AsyncMock(return_value=allowed)
    access_control.check_control_role = AsyncMock(return_value=allowed)
    access_control.grant_access = AsyncMock()
    access_control.grant_principal_session_access = AsyncMock()

    if isinstance(getattr(dao, "rebuild_admission", None), MagicMock):
        dao.rebuild_admission.is_pending = AsyncMock(return_value=False)

    app = FastAPI(dependencies=[Depends(attach_principal)])
    app.include_router(
        create_v4_router(
            get_dao=lambda: dao,
            get_access_control=lambda: access_control,
            get_composed_validation_policy=None,
        )
    )
    return app


@pytest.fixture
async def asgi_client() -> AsyncIterator[Any]:
    clients: list[httpx.AsyncClient] = []

    def create(app: FastAPI) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        )
        clients.append(client)
        return client

    yield create

    for client in clients:
        await client.aclose()


@pytest.mark.asyncio
async def test_v4_http_search_exact_vm_shape(asgi_client: Any) -> None:
    """Verify that the exact JSON request shape from the VM passes request parsing.

    Previously failed with: HTTP 422 datetime_type.
    """
    dao = MagicMock()
    dao.get_v4_session = AsyncMock(
        return_value={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_ids": ["tr_legislation"],
            "agent_id": "agent-a",
            "session_id": "session-a",
            "status": "ACTIVE",
        }
    )
    dao.search_v4_memory = AsyncMock(
        return_value=[
            {
                "artifact_id": "artifact-1",
                "candidate_id": "artifact-1",
                "assertion_id": "assertion-1",
                "entity": {"canonical_name": "Test Entity"},
                "provenance": [],
                "score": 0.95,
            }
        ]
    )

    app = _build_test_api(dao)
    client = asgi_client(app)

    vm_payload = {
        "session_id": "session-a",
        "dataset_ids": ["tr_legislation"],
        "query": "pre-rank isolation test query",
        "limit": 5,
        "valid_at": "2026-06-01T00:00:00Z",
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": "2026-12-31T23:59:59Z",
    }

    response = await client.post("/v4/memory/search", json=vm_payload)
    assert response.status_code == 200, f"Expected 200, got: {response.text}"
    body = response.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["artifact_id"] == "artifact-1"

    # Verify timezone-aware datetimes reached dao.search_v4_memory as ISO strings with timezone
    dao.search_v4_memory.assert_awaited_once_with(
        tenant_id="tenant-a",
        agent_id="agent-a",
        dataset_ids=["tr_legislation"],
        query="pre-rank isolation test query",
        limit=5,
        jurisdiction=None,
        valid_at="2026-06-01T00:00:00+00:00",
        valid_from="2026-01-01T00:00:00+00:00",
        valid_to="2026-12-31T23:59:59+00:00",
        graph_enabled=True,
        request_principal_id="principal-a",
        certification_metadata={},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_temporal_payload",
    [
        {"valid_at": "2026-06-01T00:00:00"},  # naive timestamp
        {"valid_at": "2026-06-01"},  # date-only
        {"valid_at": "not-a-date"},  # garbage string
        {"valid_at": 12345},  # integer
        {"valid_from": "2026-12-31T23:59:59Z", "valid_to": "2026-01-01T00:00:00Z"},  # inverted range
    ],
)
async def test_v4_http_search_rejects_malformed_temporal_with_422(
    asgi_client: Any, bad_temporal_payload: dict[str, Any]
) -> None:
    """Verify that HTTP search endpoint rejects malformed temporal inputs with HTTP 422."""
    dao = MagicMock()
    app = _build_test_api(dao)
    client = asgi_client(app)

    payload = {
        "session_id": "session-a",
        "dataset_ids": ["tr_legislation"],
        "query": "test query",
        **bad_temporal_payload,
    }
    response = await client.post("/v4/memory/search", json=payload)
    assert response.status_code == 422
    # Verify no raw Python tracebacks leaked
    assert "Traceback" not in response.text


# ===========================================================================
# 3. EFFECTIVE-DATE PHASE 7 REGRESSION TEST
# ===========================================================================


def _make_phase7_dao(engine: AsyncEngine) -> MemoryDAO:
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
        traverse_paths=AsyncMock(return_value=[]),
    )
    return MemoryDAO(engine, vector, graph)


async def _seed_test_assertion(
    dao: MemoryDAO,
    *,
    tenant_id: str,
    agent_id: str,
    dataset_id: str,
    doc_id: str,
    subject: str,
    predicate: str,
    literal_value: str,
    raw_log_id: int,
    valid_from: str = "",
    valid_to: str = "",
) -> dict[str, Any]:
    cand = MemoryCandidate.from_raw_log(
        raw_log_id=raw_log_id,
        tenant_id=tenant_id,
        workspace_id="workspace-eff",
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=f"rev-{doc_id}",
        chunk_id=f"chunk-{doc_id}",
        source_ref=f"source-{doc_id}",
        agent_id=agent_id,
        session_id="session-eff",
        content_payload=f"{subject} {predicate} {literal_value}",
        embedding_provider="test",
        embedding_model="catalog-contract",
        embedding_version="v1",
        embedding_dimension=2,
        embedding_space_id="test:catalog-contract:v1:2:norm=true",
        embedding_normalized=True,
    ).as_consolidation_record()
    await dao.record_mutation(cand, raw_log_id=raw_log_id)
    mut = await dao.get_projection_mutation(str(cand["mutation_id"]))
    assert mut is not None

    await dao.project_v4_sql_entity(mutation=mut, entity_name=subject)
    triplet = {
        "head": subject,
        "relation": predicate,
        "literal_value": literal_value,
        "evidence_span": f"{subject} evidence",
        "confidence": 1.0,
        "metadata": {
            "jurisdiction": "TR",
            "valid_from": valid_from,
            "valid_to": valid_to,
        },
    }
    await dao.project_v4_graph_triplet(mutation=mut, triplet=triplet)
    assertions = await dao.list_v4_assertions_for_mutation(str(mut["mutation_id"]))
    assertion = assertions[0]

    async with dao._sql.transaction() as db:
        await db.execute(
            "UPDATE v4_assertions SET status = 'ACTIVE', jurisdiction = 'TR', "
            "valid_from = ?, valid_to = ? WHERE assertion_id = ?",
            (valid_from, valid_to, assertion["assertion_id"]),
        )
        await db.commit()

    await dao.project_v4_vector_assertion(mutation=mut, assertion=assertion)
    async with dao._sql.transaction() as db:
        await db.execute(
            "UPDATE memory_mutations SET state = 'COMMITTED' WHERE mutation_id = ?",
            (mut["mutation_id"],),
        )
        await db.commit()

    return assertion


@pytest.mark.asyncio
async def test_effective_date_boundary_search_regression(
    tmp_path: Any, asgi_client: Any
) -> None:
    """Targeted regression for effective_date_boundary_search (Phase 7).

    Validates:
    1. V4 HTTP search request with ISO-8601 strings parses successfully under strict validation.
    2. Request passes schema validation and reaches temporal business logic in MemoryDAO.
    3. SQLite Julian day comparison correctly filters:
       - Active assertion matching valid_at window is returned.
       - Past assertion (expired) is excluded.
       - Future assertion (not yet effective) is excluded.
    """
    db_path = str(tmp_path / "effective_date_reg.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_phase7_dao(engine)
    tenant_id = "tenant-eff"
    agent_id = "agent-eff"
    dataset_id = "tr_legislation"
    session_id = "session-eff"

    try:
        # Seed V4 catalog scope and session
        await dao.ensure_v4_catalog_scope(
            tenant_id=tenant_id, workspace_id="workspace-eff", dataset_id=dataset_id
        )
        await dao.create_v4_session(
            tenant_id=tenant_id,
            workspace_id="workspace-eff",
            dataset_ids=[dataset_id],
            agent_id=agent_id,
            principal_id="principal-a",
            session_id=session_id,
        )

        # Seed 3 assertions across time boundaries
        # Past: valid in 2021-2023
        ass_past = await _seed_test_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-past",
            subject="Asgari Ucret",
            predicate="TUTAR",
            literal_value="5500 TL",
            raw_log_id=1,
            valid_from="2021-01-01T00:00:00Z",
            valid_to="2023-12-31T23:59:59Z",
        )

        # Current (in scope for 2026): valid in 2026
        ass_current = await _seed_test_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-current",
            subject="Asgari Ucret",
            predicate="TUTAR",
            literal_value="22104 TL",
            raw_log_id=2,
            valid_from="2026-01-01T00:00:00Z",
            valid_to="2026-12-31T23:59:59Z",
        )

        # Future: valid in 2030+
        ass_future = await _seed_test_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-future",
            subject="Asgari Ucret",
            predicate="TUTAR",
            literal_value="50000 TL",
            raw_log_id=3,
            valid_from="2030-01-01T00:00:00Z",
            valid_to="2035-12-31T23:59:59Z",
        )

        app = _build_test_api(dao)
        client = asgi_client(app)

        # Execute search through HTTP endpoint with exact Phase 7 effective date parameters
        response = await client.post(
            "/v4/memory/search",
            json={
                "session_id": session_id,
                "dataset_ids": [dataset_id],
                "query": "Asgari Ucret",
                "limit": 10,
                "valid_at": "2026-06-01T00:00:00Z",
                "valid_from": "2026-01-01T00:00:00Z",
                "valid_to": "2026-12-31T23:59:59Z",
            },
        )
        assert response.status_code == 200, f"HTTP search failed: {response.text}"
        results = response.json()["results"]

        retrieved_assertion_ids = [
            r["assertion_id"] for r in results if "assertion_id" in r
        ]
        # Current assertion must be present
        assert ass_current["assertion_id"] in retrieved_assertion_ids
        # Past and future assertions must be strictly excluded by temporal isolation
        assert ass_past["assertion_id"] not in retrieved_assertion_ids
        assert ass_future["assertion_id"] not in retrieved_assertion_ids
    finally:
        await engine.close()


# ===========================================================================
# 4. OPENAPI SCHEMA TESTS
# ===========================================================================


def test_v4_openapi_schema_temporal_types() -> None:
    """Verify that OpenAPI schema still describes temporal fields as date-time."""
    dao = MagicMock()
    app = _build_test_api(dao)
    schema = app.openapi()

    v4_search_props = schema["components"]["schemas"]["V4SearchRequest"]["properties"]
    for field_name in ("valid_at", "valid_from", "valid_to"):
        prop = v4_search_props[field_name]
        # Pydantic v2 nullable date-time field is represented as anyOf with date-time string and null
        types_in_anyof = [
            sub.get("format") or sub.get("type") for sub in prop.get("anyOf", [])
        ]
        assert "date-time" in types_in_anyof, (
            f"Field {field_name} must retain format 'date-time' in OpenAPI schema, got: {prop}"
        )


# ===========================================================================
# 5. SDK COMPATIBILITY ROUND-TRIP TEST
# ===========================================================================


@pytest.mark.asyncio
async def test_sdk_temporal_search_round_trip() -> None:
    """Verify that Python SDK search serializes ISO-8601 strings and HTTP server parses them into datetimes."""
    dao = MagicMock()
    dao.get_v4_session = AsyncMock(
        return_value={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_ids": ["tr_legislation"],
            "agent_id": "agent-a",
            "session_id": "session-a",
            "status": "ACTIVE",
        }
    )
    dao.search_v4_memory = AsyncMock(
        return_value=[{"artifact_id": "a1", "score": 1.0}]
    )

    app = _build_test_api(dao)
    transport = httpx.ASGITransport(app=app)
    client = AsyncMesaV4Client(base_url="http://test", api_key="test-key")
    client._client = httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": "Bearer test-key"},
    )

    try:
        res = await client.search(
            session_id="session-a",
            query="test query",
            dataset_ids=["tr_legislation"],
            valid_at="2026-06-01T00:00:00Z",
            valid_from="2026-01-01T00:00:00Z",
            valid_to="2026-12-31T23:59:59Z",
        )
        assert len(res["results"]) == 1
        assert res["results"][0]["artifact_id"] == "a1"

        # Verify exact round-trip conversion reached internal retrieval layer
        dao.search_v4_memory.assert_awaited_once_with(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["tr_legislation"],
            query="test query",
            limit=10,
            jurisdiction=None,
            valid_at="2026-06-01T00:00:00+00:00",
            valid_from="2026-01-01T00:00:00+00:00",
            valid_to="2026-12-31T23:59:59+00:00",
            graph_enabled=True,
            request_principal_id="principal-a",
            certification_metadata={},
        )
    finally:
        await client.aclose()
