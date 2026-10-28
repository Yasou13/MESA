import asyncio
import functools
from typing import Any, Optional, Type, Union

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
        thinking: bool = False,
        timeout_seconds: float = 300.0,
        num_ctx: int = 8192,
    ):
        if ollama is None:
            raise RuntimeError(
                'Ollama LLM support requires: pip install "mesa-memory[adapters]"'
            )
        self._model = model
        self._embedding_model = embedding_model
        self.base_url = base_url  # type: ignore[no-untyped-def]
        self._thinking = thinking
        self.timeout_seconds = timeout_seconds
        self._timeout_seconds = timeout_seconds
        self.num_ctx = num_ctx
        self._num_ctx = num_ctx
        client_kwargs: dict[str, Any] = {"timeout": timeout_seconds}
        if base_url:
            client_kwargs["host"] = base_url
        self._ollama_client = ollama.Client(**client_kwargs)

    def complete(
        self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs: Any
    ) -> Union[str, BaseModel]:
        options: dict[str, Any] = {"num_ctx": self._num_ctx}
        if schema is not None:
            options["temperature"] = 0
        max_tokens = kwargs.get("max_tokens")
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        if schema is not None:
            response = self._ollama_client.generate(
                model=self._model,
                prompt=prompt,
                format=schema.model_json_schema(),
                options=options,
                think=self._thinking,
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
            options=options,
            think=self._thinking,
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
