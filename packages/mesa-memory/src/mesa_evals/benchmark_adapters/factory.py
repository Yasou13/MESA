from typing import Any

from .base import BaseMemoryClient


def get_adapter(name: str, **kwargs: Any) -> BaseMemoryClient:
    name = name.lower()
    if name == "mesa":
        # Requires an LLM adapter
        from mesa_memory.adapter.factory import AdapterFactory

        from .mesa_adapter import MesaClient

        llm = AdapterFactory.get_adapter("auto")  # type: ignore[return-value]
        return MesaClient(llm)
    elif name == "mem0":
        from .mem0_adapter import Mem0Client  # type: ignore[return-value]

        return Mem0Client()
    elif name == "barerag":
        from mesa_memory.adapter.factory import AdapterFactory

        from .barerag_adapter import BareRAGClient  # type: ignore[return-value]

        llm = AdapterFactory.get_adapter("auto")
        return BareRAGClient(llm)  # type: ignore[return-value]
    elif name == "letta":
        from .letta_adapter import LettaAdapter

        # type: ignore[return-value]
        return LettaAdapter()
    elif name == "zep":
        from .zep_adapter import ZepAdapter

        return ZepAdapter(**kwargs)
    else:
        raise ValueError(f"Unknown adapter: {name}")
