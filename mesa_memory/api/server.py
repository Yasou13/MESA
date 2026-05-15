import os
import time

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.security import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from mesa_memory.adapter.factory import AdapterFactory
from mesa_memory.observability.metrics import ObservabilityLayer
from mesa_memory.retrieval.core import QueryAnalyzer
from mesa_memory.retrieval.hybrid import HybridRetriever
from mesa_memory.schema.cmb import CMB, ResourceCost
from mesa_memory.storage import StorageFacade
from mesa_memory.valence.core import ValenceMotor

app = FastAPI(title="MESA API", version="1.0.0")

# ---------------------------------------------------------------------------
# API Key Authentication
# ---------------------------------------------------------------------------
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
_MESA_API_KEY = os.environ.get(
    "MESA_API_KEY", "mesa-local-dev-key-do-not-use-in-production"
)


async def get_api_key(api_key: str = Depends(_API_KEY_HEADER)) -> str:
    """Validate the incoming API key against the server-side secret."""
    if not api_key or api_key != _MESA_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")
    return api_key


class AppState:
    facade: StorageFacade
    motor: ValenceMotor
    obs_layer: ObservabilityLayer
    retriever: HybridRetriever


state = AppState()


class IngestRequest(BaseModel):
    content: str
    source: str
    agent_id: str
    session_id: str


class QueryRequest(BaseModel):
    query: str
    max_results: int = 5
    session_id: str
    agent_id: str = "api_query_agent"


@app.on_event("startup")
async def startup_event():
    state.obs_layer = ObservabilityLayer()
    adapter = AdapterFactory.get_adapter()

    state.facade = StorageFacade()
    state.motor = ValenceMotor(
        llm_adapter=adapter, obs_layer=state.obs_layer, storage=state.facade.vector
    )

    await state.facade.initialize_all(valence_motor=state.motor)

    analyzer = QueryAnalyzer()
    state.retriever = HybridRetriever(
        storage_facade=state.facade,
        analyzer=analyzer,
        embedder=adapter,
        access_control=state.facade.access_control,
    )


@app.post("/ingest")
async def ingest(request: IngestRequest, _api_key: str = Depends(get_api_key)):
    if not state.facade.access_control.check_access(
        request.agent_id, request.session_id, "WRITE"
    ):
        raise HTTPException(
            status_code=403, detail="Forbidden: Insufficient RBAC privileges"
        )

    start_t = time.time()
    embedding = state.motor.llm_adapter.embed(request.content)
    latency = (time.time() - start_t) * 1000

    token_count = state.motor.llm_adapter.get_token_count(request.content)

    from mesa_memory.valence.fitness import calculate_fitness_score

    fitness_score = calculate_fitness_score(request.content, token_count)

    cmb = CMB(
        content_payload=request.content,
        source=request.source,
        performative="INFORM",
        resource_cost=ResourceCost(token_count=token_count, latency_ms=latency),
        embedding=embedding,
        fitness_score=fitness_score,
    )

    decision = await state.motor.evaluate(cmb.model_dump(), {"error": False})

    if decision is False:
        return {"status": "DISCARDED"}

    if decision == "DEFERRED":
        cmb.tier3_deferred = True

    await state.facade.persist_cmb(cmb, request.agent_id, request.session_id)
    return {
        "status": "STORED" if decision is True else "DEFERRED",
        "cmb_id": cmb.cmb_id,
    }


@app.post("/query")
async def query(request: QueryRequest, _api_key: str = Depends(get_api_key)):
    if not state.facade.access_control.check_access(
        request.agent_id, request.session_id, "READ"
    ):
        raise HTTPException(
            status_code=403, detail="Forbidden: Insufficient RBAC privileges"
        )

    results_ids = await state.retriever.retrieve(
        query_text=request.query,
        agent_id=request.agent_id,
        session_id=request.session_id,
        top_n=request.max_results,
    )

    results = []
    for rid in results_ids:
        cmb = await state.facade.get_cmb(rid, request.agent_id, request.session_id)
        if cmb:
            results.append(cmb)

    return {"results": results}


@app.get("/health")
async def health():
    return state.obs_layer.get_health_status()


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
