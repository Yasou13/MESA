import hashlib
import json
import math
import random
import re
from typing import Optional, Type, Union

from pydantic import BaseModel

from mesa_memory.adapter.base import BaseUniversalLLMAdapter


class DeterministicMockAdapter(BaseUniversalLLMAdapter):
    """
    A deterministic mock adapter for testing and demonstrations.
    Provides stable embeddings via SHA-256 and simplistic entity extraction
    for fallback triplets without needing a real model.
    """

    def embed(self, text: str, **kwargs) -> list[float]:
        EMBEDDING_DIM = 384
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16) % (2**32)
        rng = random.Random(seed)
        raw = [rng.gauss(0, 1) for _ in range(EMBEDDING_DIM)]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]

    async def aembed(self, text: str, **kwargs) -> list[float]:
        return self.embed(text, **kwargs)

    def embed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        return [self.embed(text, **kwargs) for text in texts]

    async def aembed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        return self.embed_batch(texts, **kwargs)

    def complete(self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs) -> Union[str, BaseModel]:
        if "decision" in prompt and "justification" in prompt:
            res = {"decision": "STORE", "justification": "Mock STORE decision"}
        elif "triplets" in prompt and "record_index" in prompt:
            indices = re.findall(r'=== RECORD (\d+) ===', prompt)
            trips = []
            for idx in indices:
                trips.append({
                    "record_index": int(idx),
                    "head": "MockHead",
                    "relation": "RELATES_TO",
                    "tail": "MockTail",
                    "confidence": 0.9
                })
            res = {"triplets": trips}
        elif "Context:" in prompt and "Query:" in prompt:
            return "Mock Final Report: The extraction and retrieval were successful."
        else:
            words = [w for w in re.split(r'\W+', prompt) if w]
            first = words[0] if words else "Unknown"
            last = words[-1] if len(words) > 1 else first
            res = {"head": first, "relation": "RELATES_TO", "tail": last}

        json_str = json.dumps(res)

        if schema is not None:
            try:
                return schema.model_validate_json(json_str)
            except Exception:
                pass

        return json_str

    async def acomplete(self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs) -> Union[str, BaseModel]:
        return self.complete(prompt, schema=schema, **kwargs)

    def get_token_count(self, text: str) -> int:
        return len(text.split())
