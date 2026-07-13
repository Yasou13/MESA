#!/usr/bin/env python3
import os
import time

import uvicorn
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mesa_memory.adapter.factory import AdapterFactory

# Import the main MESA app and state
from scripts.run_server import _project_root, _state, app

# ---------------------------------------------------------------------------
# Demo Static Files
# ---------------------------------------------------------------------------
_demo_path = os.path.join(_project_root, "demo")
if os.path.isdir(_demo_path):
    app.mount("/demo", StaticFiles(directory=_demo_path, html=True), name="demo")


# ---------------------------------------------------------------------------
# Demo RAG Endpoint
# ---------------------------------------------------------------------------
class DemoChatRequest(BaseModel):
    agent_id: str
    session_id: str
    query: str


class DemoChatResponse(BaseModel):
    response_text: str
    context: list[dict]
    latency_ms: int


@app.post("/v3/demo/chat", response_model=DemoChatResponse)
async def demo_chat(req: DemoChatRequest):
    if not _state.dao:
        return JSONResponse(
            status_code=503, content={"error": "Database not initialized"}
        )

    adapter = AdapterFactory.get_adapter()

    # 1. Search memory (needs vector)
    start_t = time.time()
    query_vector = await adapter.aembed(req.query)
    search_res = await _state.dao.search_memory(
        req.agent_id, query_vector=query_vector, limit=3
    )
    latency = int((time.time() - start_t) * 1000)

    # Format context for LLM (search_res is list of dicts)
    context_str = "\n".join(
        [
            f"- {r.get('entity_name', 'Unknown')} (Score: {r.get('_distance', 0.0):.3f})"
            for r in search_res
        ]
    )

    prompt = f"""You are MESA, an intelligent memory agent.
User Query: {req.query}

Retrieved Context from Memory:
{context_str}

Instruction: Answer the user's query conversationally. Use the retrieved context if relevant. Keep it brief and friendly."""

    try:
        llm_resp = await adapter.acomplete(prompt)
    except Exception as e:
        llm_resp = f"[LLM Generation Error: {str(e)}]"

    telemetry = [
        {
            "entity": r.get("entity_name", "Unknown"),
            "score": float(r.get("_distance", 0.0)),
        }
        for r in search_res
    ]

    return DemoChatResponse(
        response_text=str(llm_resp), context=telemetry, latency_ms=latency
    )


if __name__ == "__main__":
    print(f"\n{'=' * 60}")
    print("  MESA RAG Demo Server (Wraps Main Server)")
    print("  Bind:    0.0.0.0:8000")
    print(f"{'=' * 60}\n")
    uvicorn.run(
        "scripts.run_demo_rag:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
