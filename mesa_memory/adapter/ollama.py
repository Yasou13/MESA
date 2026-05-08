import asyncio
import functools
from typing import Optional, Type, Union

import ollama
import outlines
from pydantic import BaseModel

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.adapter.tokenizer import count_tokens
from mesa_memory.config import config


class OllamaAdapter(BaseUniversalLLMAdapter):
    EMBEDDING_DIM = 768

    def __init__(self, model: str = "mistral", embedding_model: str = "nomic-embed-text"):
        self._model = model
        self._embedding_model = embedding_model
        self._ollama_client = ollama.Client()

    def complete(self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs) -> Union[str, BaseModel]:
        if schema is not None:
            llm = outlines.models.transformers(self._model)
            generator = outlines.generate.json(llm, schema)
            return generator(prompt)

        response = self._ollama_client.generate(
            model=self._model,
            prompt=prompt,
        )
        return response["response"]

    async def acomplete(self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs) -> Union[str, BaseModel]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(self.complete, prompt, schema, **kwargs),
        )

    def embed(self, text: str, **kwargs) -> list[float]:
        response = self._ollama_client.embeddings(
            model=self._embedding_model,
            prompt=text,
        )
        return response["embedding"]

    async def aembed(self, text: str, **kwargs) -> list[float]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(self.embed, text, **kwargs),
        )

    def get_token_count(self, text: str) -> int:
        return count_tokens(text, adapter_type="ollama", model_id=self._model)
