from typing import Any

from .base import BaseMemoryClient


def get_adapter(name: str, **kwargs: Any) -> BaseMemoryClient:
    name = name.lower()
    if name == "mesa":
        from .mesa_adapter import MesaClient
        # Requires an LLM adapter
        from mesa_memory.adapter.factory import AdapterFactory

        llm = AdapterFactory.get_adapter("auto")
        return MesaClient(llm)
    elif name == "mem0":
        from .mem0_adapter import Mem0Client
        return Mem0Client()
    elif name == "barerag":
        from .barerag_adapter import BareRAGClient
        from mesa_memory.adapter.factory import AdapterFactory

        llm = AdapterFactory.get_adapter("auto")
        return BareRAGClient(llm)
    elif name == "letta":
        from .letta_adapter import LettaAdapter
        return LettaAdapter()
    elif name == "zep":
        from .zep_adapter import ZepAdapter
        return ZepAdapter(**kwargs)
    else:
        raise ValueError(f"Unknown adapter: {name}")
