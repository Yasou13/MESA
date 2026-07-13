#!/usr/bin/env python3
"""MESA RAG Demo Server.

Extends the main MESA FastAPI app with:
  - ``/demo``         — Static file serving for the demo UI
  - ``/v3/demo/chat`` — End-to-end RAG endpoint (search → LLM → respond)

Usage:
    PYTHONPATH=. python scripts/run_demo_rag.py
"""

import os
import time
from typing import Optional

import uvicorn
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mesa_memory.adapter.factory import AdapterFactory

# Import the main MESA app and shared state
from scripts.run_server import _project_root, _state, app

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


# ---------------------------------------------------------------------------
# POST /v3/demo/chat — RAG endpoint
# ---------------------------------------------------------------------------
@app.post("/v3/demo/chat", response_model=DemoChatResponse)
async def demo_chat(req: DemoChatRequest):
    """Search MESA memory → build context → call LLM → return response + telemetry."""

    if not _state.dao:
        return JSONResponse(
            status_code=503, content={"error": "Database not initialized"}
        )

    adapter = _get_adapter()

    # 1. Embed query and search memory
    start_t = time.time()
    query_vector = await adapter.aembed(req.query)
    search_res = await _state.dao.search_memory(
        req.agent_id, query_vector=query_vector, limit=5
    )
    search_ms = int((time.time() - start_t) * 1000)

    # 2. Build context string for LLM
    context_lines = []
    for r in search_res:
        name = r.get("entity_name", "—")
        content = r.get("content", "")
        dist = r.get("_distance", 0.0)
        context_lines.append(f"• [{name}] (distance={dist:.3f}): {content}")
    context_str = "\n".join(context_lines) if context_lines else "(boş — geçmiş kayıt yok)"

    # 3. System prompt
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

    # 4. LLM generation
    llm_start = time.time()
    try:
        llm_resp = await adapter.acomplete(prompt)
    except Exception as e:
        llm_resp = f"[LLM Error: {e}]"
    total_ms = search_ms + int((time.time() - llm_start) * 1000)

    # 5. Build telemetry payload
    telemetry = [
        {
            "entity": r.get("entity_name", "Unknown"),
            "score": float(r.get("_distance", 0.0)),
        }
        for r in search_res
    ]

    return DemoChatResponse(
        response_text=str(llm_resp),
        context=telemetry,
        latency_ms=total_ms,
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
