from abc import ABC, abstractmethod
from typing import Optional, Type, Union

from pydantic import BaseModel

from mesa_memory.config import config


class TokenBudgetExceededError(Exception):
    pass


class BaseUniversalLLMAdapter(ABC):
    @property
    def EMBEDDING_DIM(self) -> int:
        return config.embedding_dimension

    @abstractmethod
    def complete(  # type: ignore[no-untyped-def]
        self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs
    ) -> Union[str, BaseModel]: ...

    @abstractmethod
    async def acomplete(  # type: ignore[no-untyped-def]
        self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs
    ) -> Union[str, BaseModel]: ...

    @abstractmethod
    def embed(self, text: str, **kwargs) -> list[float]: ...  # type: ignore[no-untyped-def]

    @abstractmethod
    async def aembed(self, text: str, **kwargs) -> list[float]: ...  # type: ignore[no-untyped-def]

    @abstractmethod
    def embed_batch(self, texts: list[str], **kwargs) -> list[list[float]]: ...  # type: ignore[no-untyped-def]

    @abstractmethod
    async def aembed_batch(self, texts: list[str], **kwargs) -> list[list[float]]: ...  # type: ignore[no-untyped-def]

    @abstractmethod
    def get_token_count(self, text: str) -> int: ...
