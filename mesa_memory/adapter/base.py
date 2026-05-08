from abc import ABC, abstractmethod
from typing import Optional, Type, Union, Any

from pydantic import BaseModel


class TokenBudgetExceededError(Exception):
    pass


class BaseUniversalLLMAdapter(ABC):
    EMBEDDING_DIM: int

    @abstractmethod
    def complete(self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs) -> Union[str, BaseModel]:
        ...

    @abstractmethod
    async def acomplete(self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs) -> Union[str, BaseModel]:
        ...

    @abstractmethod
    def embed(self, text: str, **kwargs) -> list[float]:
        ...

    @abstractmethod
    async def aembed(self, text: str, **kwargs) -> list[float]:
        ...

    @abstractmethod
    def get_token_count(self, text: str) -> int:
        ...
