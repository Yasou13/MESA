from .base import AbstractBenchmarkClient, BenchmarkResponse
from .dense_rag_client import DenseRagClientAdapter
from .dummy_client import DummyClientAdapter
from .mem0_client import Mem0ClientAdapter

# Optional competitor adapters — fail gracefully if dependencies missing
try:
    from .zep_client import ZepClientAdapter
except ImportError:
    ZepClientAdapter = None  # type: ignore[assignment,misc]

try:
    from .letta_client import LettaClientAdapter
except ImportError:
    LettaClientAdapter = None  # type: ignore[assignment,misc]

__all__ = [
    "AbstractBenchmarkClient",
    "BenchmarkResponse",
    "DenseRagClientAdapter",
    "DummyClientAdapter",
    "LettaClientAdapter",
    "Mem0ClientAdapter",
    "ZepClientAdapter",
]
