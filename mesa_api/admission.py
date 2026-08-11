"""Shared API admission policy for storage-root mutations."""

from fastapi import HTTPException

from mesa_storage.dao import MemoryDAO


async def require_mutation_admission(
    dao: MemoryDAO, *, require_projection_consumer: bool = False
) -> None:
    """Fail closed while a durable projection rebuild owns the storage root."""
    if require_projection_consumer and dao.canonical_v4_writes_enabled is False:
        raise HTTPException(
            status_code=503,
            detail="canonical_processing_unavailable",
            headers={"Retry-After": "5"},
        )
    if await dao.rebuild_admission.is_pending():
        raise HTTPException(
            status_code=503,
            detail="maintenance_pending",
            headers={"Retry-After": "5"},
        )
