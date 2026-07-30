"""Opt-in local Showcase RAG route."""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable, Protocol

from fastapi import APIRouter, Depends, HTTPException

from mesa_contracts.demo import DemoChatRequest, DemoChatResponse
from mesa_memory.adapter.factory import AdapterFactory
from mesa_memory.config import RuntimeEnvironment, RuntimeProfileConfig
from mesa_storage.dao import MemoryDAO

logger = logging.getLogger("MESA_Showcase")


class DemoModelAdapter(Protocol):
    def aembed(self, text: str) -> Awaitable[list[float]]: ...

    def acomplete(self, prompt: str) -> Awaitable[object]: ...


def ensure_demo_mode(settings: RuntimeProfileConfig | None) -> None:
    """Hide the direct-write route unless the operator explicitly opts in."""
    if (
        settings is None
        or not settings.showcase_demo_enabled
        or settings.environment
        not in {RuntimeEnvironment.DEVELOPMENT, RuntimeEnvironment.TEST}
    ):
        raise HTTPException(status_code=404, detail="Showcase demo is disabled")


def create_demo_router(
    *,
    get_dao: Callable[[], MemoryDAO],
    get_settings: Callable[[], RuntimeProfileConfig | None],
    auth_dependency: Callable,
) -> APIRouter:
    router = APIRouter(prefix="/v3/demo", tags=["showcase"])
    adapter: DemoModelAdapter | None = None

    def require_demo_mode() -> None:
        ensure_demo_mode(get_settings())

    def get_adapter() -> DemoModelAdapter:
        nonlocal adapter
        if adapter is None:
            adapter = AdapterFactory.get_adapter()
        return adapter

    @router.post(
        "/chat",
        response_model=DemoChatResponse,
        dependencies=[Depends(require_demo_mode), Depends(auth_dependency)],
    )
    async def demo_chat(payload: DemoChatRequest) -> DemoChatResponse:
        dao = get_dao()
        model_adapter = get_adapter()
        started = time.perf_counter()
        query_vector = await model_adapter.aembed(payload.query)

        memory_stored = False
        try:
            embedding = await dao.vector_engine.compute_embedding(payload.query[:512])
            await dao.insert_memory(
                payload.agent_id,
                entity_name=payload.query[:256],
                content=payload.query,
                embedding=embedding,
                node_type="MEMORY",
                session_id=payload.session_id,
            )
            memory_stored = True
        except Exception:
            logger.exception("SHOWCASE_DIRECT_WRITE_FAILED")

        search_results = await dao.search_memory(
            payload.agent_id, query_vector=query_vector, limit=5
        )
        context_lines: list[str] = []
        for result in search_results:
            if float(result.get("_distance", 0.0)) < 0.01:
                continue
            graph = result.get("graph", {})
            context_lines.append(
                f"• [{graph.get('entity_name', '—')}] "
                f"(benzerlik={1 - float(result.get('_distance', 0.0)):.1%}): "
                f"{graph.get('content_payload', '')}"
            )
        context = "\n".join(context_lines) or "(boş — geçmiş kayıt yok)"
        prompt = (
            "Sen MESA adında kurumsal bir bellek ajanısın. Kullanıcının dilinde, "
            "bellek bağlamına dayanan 2-3 cümlelik dürüst bir yanıt ver.\n\n"
            f"Kullanıcı: {payload.query}\n\nBellek Bağlamı:\n{context}"
        )
        try:
            response_text = str(await model_adapter.acomplete(prompt))
        except Exception as exc:
            logger.warning("SHOWCASE_LLM_FAILED | type=%s", type(exc).__name__)
            response_text = "Demo modeli şu anda yanıt üretemiyor."

        telemetry = [
            {
                "entity": result.get("graph", {}).get("entity_name", "Unknown"),
                "score": float(result.get("_distance", 0.0)),
            }
            for result in search_results
            if float(result.get("_distance", 0.0)) >= 0.01
        ]
        return DemoChatResponse(
            response_text=response_text,
            context=telemetry,
            latency_ms=int((time.perf_counter() - started) * 1000),
            memory_stored=memory_stored,
        )

    return router
