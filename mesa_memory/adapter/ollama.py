import asyncio
import functools
from typing import Optional, Type, Union

try:
    import ollama
except ImportError:
    ollama = None  # type: ignore[assignment]
from pydantic import BaseModel

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.adapter.tokenizer import count_tokens


class OllamaAdapter(BaseUniversalLLMAdapter):
    def __init__(
        self,
        model: str = "mistral",
        embedding_model: str = "nomic-embed-text",
        base_url: Optional[str] = None,
    ):
        if ollama is None:
            raise RuntimeError("OllamaAdapter requires mesa-memory[adapters]")
        self._model = model
        self._embedding_model = embedding_model
        self.base_url = base_url  # type: ignore[no-untyped-def]
        self._ollama_client = (
            ollama.Client(host=base_url) if base_url else ollama.Client()
        )

    def complete(
        self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs
    ) -> Union[str, BaseModel]:
        if schema is not None:
            response = self._ollama_client.generate(
                model=self._model,
                prompt=prompt,
                format=schema.model_json_schema(),
            )
            try:
                return schema.model_validate_json(response["response"])
            except Exception as e:
                # Fallback if Ollama returns malformed JSON
                import logging

                logging.getLogger(__name__).warning(
                    f"Ollama returned invalid JSON for schema: {e}"
                )
                raise
        # type: ignore[no-any-return]
        response = self._ollama_client.generate(
            model=self._model,  # type: ignore[no-untyped-def]
            prompt=prompt,
        )
        return response["response"]

    async def acomplete(
        self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs
    ) -> Union[str, BaseModel]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(  # type: ignore[no-untyped-def]
            None,
            functools.partial(self.complete, prompt, schema, **kwargs),
        )

    def embed(self, text: str, **kwargs) -> list[float]:  # type: ignore[float]
        response = self._ollama_client.embeddings(
            model=self._embedding_model,  # type: ignore[no-untyped-def]
            prompt=text,
        )
        return response["embedding"]

    async def aembed(self, text: str, **kwargs) -> list[float]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(  # type: ignore[no-untyped-def]
            None,
            functools.partial(self.embed, text, **kwargs),
        )  # type: ignore[no-untyped-def]

    def embed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        return [self.embed(t, **kwargs) for t in texts]

    async def aembed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(self.embed_batch, texts, **kwargs),
        )

    def get_token_count(self, text: str) -> int:
        return count_tokens(text, adapter_type="ollama", model_id=self._model)
