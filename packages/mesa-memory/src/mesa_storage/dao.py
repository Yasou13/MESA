"""Compatibility facade for the pre-0.8 unified storage interface.

New application code should depend on the capability Protocols in
``mesa_memory.ports``. The implementation lives behind ``StorageKernel`` so
this facade contains no SQL and performs no direct backend access.
"""

from __future__ import annotations

from mesa_storage.storage_kernel import (
    PurgeAlreadyFinalizedError,
    PurgeBlockedError,
    PurgeRetryPendingError,
    QueueAdmissionError,
    QueueOverCapacityError,
    QueueRecordTooLargeError,
    QueueUnavailableError,
    StorageKernel,
    _assert_valid_agent_id,
    _public_tier3_audit,
)


class MemoryDAO(StorageKernel):
    """0.8–0.9 compatibility facade preserving the existing public surface."""


__all__ = [
    "MemoryDAO",
    "PurgeAlreadyFinalizedError",
    "PurgeBlockedError",
    "PurgeRetryPendingError",
    "QueueAdmissionError",
    "QueueOverCapacityError",
    "QueueRecordTooLargeError",
    "QueueUnavailableError",
    "_public_tier3_audit",
    "_assert_valid_agent_id",
]
