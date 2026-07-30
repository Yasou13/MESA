class BenchmarkError(Exception):
    """Base exception for all MESA Benchmark Suite errors."""

    pass


class ConfigurationError(BenchmarkError):
    """Raised when there is an issue with the configuration (YAML or Pydantic validation)."""

    pass


class ClientTimeoutError(BenchmarkError):
    """Raised when a target memory system (client) fails to respond within the expected time."""

    pass


class StateError(BenchmarkError):
    """Raised when there is an issue reading, writing, or interpreting the execution state."""

    pass


class ClientConnectionError(BenchmarkError):
    """Raised when unable to connect to the target memory system."""

    pass
