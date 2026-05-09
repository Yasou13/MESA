import asyncio
from typing import Optional, Type, Union

from pydantic import BaseModel

from mesa_memory.storage import StorageFacade
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.schema.cmb import CMB, ResourceCost
from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.observability.metrics import ObservabilityLayer
from mesa_memory.consolidation.schemas import BatchExtractionResponse, ExtractedTriplet


class MockOllamaAdapter(BaseUniversalLLMAdapter):
    def complete(self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs) -> Union[str, BaseModel]:
        if schema == BatchExtractionResponse:
            return BatchExtractionResponse(triplets=[
                ExtractedTriplet(record_index=0, head="Patient", relation="has_symptom", tail="Headache")
            ])
        return '{"head": "Patient", "relation": "has_symptom", "tail": "Headache"}'
        
    async def acomplete(self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs) -> Union[str, BaseModel]:
        return self.complete(prompt, schema, **kwargs)
        
    def embed(self, text: str, **kwargs) -> list[float]:
        return [0.1] * self.EMBEDDING_DIM
        
    async def aembed(self, text: str, **kwargs) -> list[float]:
        return self.embed(text, **kwargs)
        
    def get_token_count(self, text: str) -> int:
        return 10


async def main():
    print("Initializing MESA StorageFacade...")
    storage = StorageFacade(
        raw_log_path="./examples_storage/raw_log.db",
        vector_uri="./examples_storage/vector_index.lance",
        graph_db_path="./examples_storage/knowledge_graph.db",
        graph_rocks_path="./examples_storage/kg_history.rocks"
    )
    await storage.initialize_all()
    
    mock_adapter = MockOllamaAdapter()
    obs_layer = ObservabilityLayer()
    
    print("Initializing ConsolidationLoop...")
    loop = ConsolidationLoop(
        storage_facade=storage,
        embedder=mock_adapter,
        llm_a=mock_adapter,
        llm_b=mock_adapter,
        obs_layer=obs_layer
    )
    
    # Create a single mock Cognitive Memory Block (CMB)
    mock_cmb = CMB(
        content_payload="Patient reports a severe headache.",
        source="Clinical Note",
        performative="inform",
        resource_cost=ResourceCost(token_count=10, latency_ms=50.0),
        embedding=mock_adapter.embed("Patient reports a severe headache.")
    )
    
    print("Storing CMB in Raw Log...")
    await storage.persist_cmb(mock_cmb, agent_id="system", session_id="session_1")
    
    print("Running Consolidation Batch...")
    records = await storage.raw_log.fetch_unconsolidated(limit=10)
    await loop.run_batch(records)
    print("Batch run complete! Triplet extracted and validated.")

if __name__ == "__main__":
    asyncio.run(main())
