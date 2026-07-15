#!/usr/bin/env python3
"""MESA RAG Demo Server.

Extends the main MESA FastAPI app with:
  - ``/demo``         — Static file serving for the demo UI
  - ``/v3/demo/chat`` — End-to-end RAG endpoint (embed → insert → search → LLM)

The key architectural difference from the production pipeline:
  The demo endpoint performs a **direct-write** into the vector store
  (bypassing the cold-path ECOD/REBEL pipeline) so that user messages
  are immediately retrievable on the *next* query.  This eliminates
  the "Context Amnesia" problem where the cold-path background task
  hasn't finished processing by the time the user asks a follow-up.

Usage:
    PYTHONPATH=. python scripts/run_demo_rag.py
"""

import logging
import os
import time
from typing import Optional

import uvicorn
from fastapi import Depends
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mesa_memory.adapter.factory import AdapterFactory
from mesa_memory.api.server import get_api_key

# Import the main MESA app and shared state
from scripts.run_server import _project_root, _state, app

logger = logging.getLogger("MESA_DemoRAG")

# ---------------------------------------------------------------------------
# Mount demo static files
# ---------------------------------------------------------------------------
_demo_path = os.path.join(_project_root, "demo")
if os.path.isdir(_demo_path):
    app.mount("/demo", StaticFiles(directory=_demo_path, html=True), name="demo")

# ---------------------------------------------------------------------------
# Adapter singleton (avoid re-creating on every request)
# ---------------------------------------------------------------------------
_adapter: Optional[object] = None


def _get_adapter():
    global _adapter
    if _adapter is None:
        _adapter = AdapterFactory.get_adapter()
    return _adapter


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class DemoChatRequest(BaseModel):
    agent_id: str
    session_id: str
    query: str


class DemoChatResponse(BaseModel):
    response_text: str
    context: list[dict]
    latency_ms: int
    memory_stored: bool


# ---------------------------------------------------------------------------
# POST /v3/demo/chat — RAG endpoint with direct-write memory
# ---------------------------------------------------------------------------
@app.post(
    "/v3/demo/chat",
    response_model=DemoChatResponse,
    dependencies=[Depends(get_api_key)],
)
async def demo_chat(req: DemoChatRequest):
    """Direct-write RAG: embed → store → search → LLM → respond.

    Unlike the production cold-path pipeline (insert_raw_log → ECOD →
    REBEL → graph commit), this endpoint writes the user's message
    directly into the vector store *before* searching, guaranteeing
    that the message is retrievable on the very next query.
    """

    if not _state.dao:
        return JSONResponse(
            status_code=503, content={"error": "Database not initialized"}
        )

    adapter = _get_adapter()
    t0 = time.time()

    # ------------------------------------------------------------------
    # 1. Embed the user's query
    # ------------------------------------------------------------------
    query_vector = await adapter.aembed(req.query)

    # ------------------------------------------------------------------
    # 2. DIRECT-WRITE: Store the user's message immediately
    #    This bypasses the cold-path (ECOD/REBEL) but guarantees
    #    the message is searchable on the next turn.
    # ------------------------------------------------------------------
    memory_stored = False
    try:
        embedding = await _state.dao.vector_engine.compute_embedding(req.query[:512])
        await _state.dao.insert_memory(
            req.agent_id,
            entity_name=req.query[:256],
            content=req.query,
            embedding=embedding,
            node_type="MEMORY",
            session_id=req.session_id,
        )
        memory_stored = True
        logger.info(
            "DEMO_DIRECT_WRITE | agent=%s session=%s len=%d",
            req.agent_id,
            req.session_id,
            len(req.query),
        )
    except Exception as e:
        import traceback

        print(f"DEMO_DIRECT_WRITE_FAILED | error={e}")
        traceback.print_exc()

    # ------------------------------------------------------------------
    # 3. Search memory for relevant context
    # ------------------------------------------------------------------
    search_res = await _state.dao.search_memory(
        req.agent_id, query_vector=query_vector, limit=5
    )

    search_ms = int((time.time() - t0) * 1000)

    # ------------------------------------------------------------------
    # 4. Build context string for LLM
    # ------------------------------------------------------------------
    context_lines = []
    for r in search_res:
        graph_data = r.get("graph", {})
        name = graph_data.get("entity_name", "—")
        content = graph_data.get("content_payload", "")
        dist = r.get("_distance", 0.0)
        # Skip the message we just inserted (distance ≈ 0)
        if dist < 0.01:
            continue
        context_lines.append(f"• [{name}] (benzerlik={1-dist:.1%}): {content}")

    context_str = (
        "\n".join(context_lines) if context_lines else "(boş — geçmiş kayıt yok)"
    )

    # ------------------------------------------------------------------
    # 5. LLM generation
    # ------------------------------------------------------------------
    prompt = f"""Sen MESA adında kurumsal bir bellek ajanısın.
Kullanıcının girdisini ve veritabanından getirilen bağlamı analiz ederek
doğal, kısa ve kullanıcının dilinde yanıt üretirsin.

Kullanıcı: {req.query}

Bellek Bağlamı:
{context_str}

Kurallar:
- Kullanıcının dilinde yanıt ver (Türkçe veya İngilizce).
- Robotik veya kalıp cümleler kullanma.
- Bağlam boşsa "belleğimde henüz bir kayıt yok" gibi dürüst ama kısa bir açıklama yap.
- Bağlam varsa onu referans alarak cevap ver.
- Cevabını 2-3 cümle ile sınırla."""

    llm_t0 = time.time()
    try:
        llm_resp = await adapter.acomplete(prompt)
    except Exception as e:
        llm_resp = f"[LLM Error: {e}]"
    total_ms = search_ms + int((time.time() - llm_t0) * 1000)

    # ------------------------------------------------------------------
    # 6. Build telemetry (exclude the self-match we just inserted)
    # ------------------------------------------------------------------
    telemetry = [
        {
            "entity": r.get("graph", {}).get("entity_name", "Unknown"),
            "score": float(r.get("_distance", 0.0)),
        }
        for r in search_res
        if r.get("_distance", 0.0) >= 0.01  # skip self-match
    ]

    return DemoChatResponse(
        response_text=str(llm_resp),
        context=telemetry,
        latency_ms=total_ms,
        memory_stored=memory_stored,
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"\n{'=' * 60}")
    print("  MESA RAG Demo Server")
    print("  Bind:    0.0.0.0:8000")
    print("  UI:      http://localhost:8000/demo/")
    print(f"{'=' * 60}\n")
    uvicorn.run(
        "scripts.run_demo_rag:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
