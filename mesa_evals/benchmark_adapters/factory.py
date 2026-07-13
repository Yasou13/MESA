from typing import Any

from .barerag_adapter import BareRAGClient
from .base import BaseMemoryClient
from .letta_adapter import LettaAdapter
from .mem0_adapter import Mem0Client
from .mesa_adapter import MesaClient
from .zep_adapter import ZepAdapter


def get_adapter(name: str, **kwargs: Any) -> BaseMemoryClient:
    name = name.lower()
    if name == "mesa":
        # Requires an LLM adapter
        from mesa_memory.adapter.factory import AdapterFactory

        llm = AdapterFactory.get_adapter("auto")
        return MesaClient(llm)
    elif name == "mem0":
        return Mem0Client()
    elif name == "barerag":
        from mesa_memory.adapter.factory import AdapterFactory

        llm = AdapterFactory.get_adapter("auto")
        return BareRAGClient(llm)
    elif name == "letta":
        return LettaAdapter()
    elif name == "zep":
        return ZepAdapter(**kwargs)
    else:
        raise ValueError(f"Unknown adapter: {name}")
