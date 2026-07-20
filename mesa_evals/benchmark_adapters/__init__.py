from typing import TYPE_CHECKING

from .base import BaseMemoryClient, QueryResult

if TYPE_CHECKING:
    from .barerag_adapter import BareRAGClient
    from .letta_adapter import LettaAdapter
    from .mem0_adapter import Mem0Client
    from .mesa_adapter import MesaClient
    from .zep_adapter import ZepAdapter

__all__ = [
    "BaseMemoryClient",
    "QueryResult",
    "BareRAGClient",
    "Mem0Client",
    "MesaClient",
    "LettaAdapter",
    "ZepAdapter",
]


def __getattr__(name: str):
    """Load optional benchmark integrations only when explicitly requested."""
    modules = {
        "BareRAGClient": (".barerag_adapter", "BareRAGClient"),
        "Mem0Client": (".mem0_adapter", "Mem0Client"),
        "MesaClient": (".mesa_adapter", "MesaClient"),
        "LettaAdapter": (".letta_adapter", "LettaAdapter"),
        "ZepAdapter": (".zep_adapter", "ZepAdapter"),
    }
    if name in modules:
        from importlib import import_module

        module_name, attribute = modules[name]
        return getattr(import_module(module_name, __name__), attribute)
    raise AttributeError(name)
