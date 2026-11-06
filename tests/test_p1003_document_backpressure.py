"""Whole-document queue pressure must not force a new upload/chunk plan."""

import asyncio

import httpx
import pytest

from mesa_client.client import AsyncMesaV4Client, MesaV4Client
from mesa_storage.dao import QueueOverCapacityError
from tests.test_document_ingestion import _payload
from tests.test_model_optional_runtime import model_runtime as model_runtime


@pytest.mark.asyncio
async def test_document_waits_for_transient_queue_room_without_replanning(
    model_runtime, monkeypatch
):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        original = env.dao.admit_v4_memory
        waits = 0

        async def pressure(self, **kwargs):
            nonlocal waits
            if kwargs["chunk_ordinal"] == 1 and waits < 2:
                waits += 1
                raise QueueOverCapacityError("tenant")
            return await original(**kwargs)

        monkeypatch.setattr(type(env.dao), "admit_v4_memory", pressure)
        response = await env.client.post(
            "/v4/documents", json=_payload(env, "Durable memory. " * 600)
        )
        assert response.status_code == 202, response.text
        assert waits == 2
        body = response.json()
        assert body["chunk_count"] > 1
        assert (
            len(body["mutation_ids"])
            == len(set(body["mutation_ids"]))
            == body["chunk_count"]
        )


@pytest.mark.asyncio
async def test_persistent_queue_pressure_still_returns_503(model_runtime, monkeypatch):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:

        async def pressure(self, **kwargs):
            raise QueueOverCapacityError("tenant")

        monkeypatch.setattr(type(env.dao), "admit_v4_memory", pressure)
        response = await asyncio.wait_for(
            env.client.post("/v4/documents", json=_payload(env, "Durable memory.")),
            timeout=7,
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "queue_over_capacity"
        async with env.dao._sql.connection() as db:
            async with db.execute("SELECT COUNT(*) FROM memory_mutations") as cursor:
                assert (await cursor.fetchone())[0] == 0


def test_sync_document_read_timeout_uses_document_deadline():
    def handle(request):
        assert 10 < request.extensions["timeout"]["read"] <= 60
        return httpx.Response(202, json={"document_id": "doc"})

    with MesaV4Client() as client:
        client._client.close()
        client._client = httpx.Client(
            base_url="http://test", transport=httpx.MockTransport(handle)
        )
        assert (
            client.add_document(
                session_id="s",
                dataset_id="d",
                document_id="doc",
                title="Doc",
                source_ref="file://doc",
                content="text",
                deadline=60,
            )["document_id"]
            == "doc"
        )


@pytest.mark.asyncio
async def test_async_document_read_timeout_uses_document_deadline():
    def handle(request):
        assert 10 < request.extensions["timeout"]["read"] <= 60
        return httpx.Response(202, json={"document_id": "doc"})

    async with AsyncMesaV4Client() as client:
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://test", transport=httpx.MockTransport(handle)
        )
        assert (
            await client.add_document(
                session_id="s",
                dataset_id="d",
                document_id="doc",
                title="Doc",
                source_ref="file://doc",
                content="text",
                deadline=60,
            )
        )["document_id"] == "doc"
