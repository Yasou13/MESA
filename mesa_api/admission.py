"""Shared API admission policy for storage-root mutations."""

from fastapi import HTTPException

from mesa_storage.dao import MemoryDAO


async def require_mutation_admission(dao: MemoryDAO) -> None:
    """Fail closed while a durable projection rebuild owns the storage root."""
    if await dao.rebuild_admission.is_pending():
        raise HTTPException(
            status_code=503,
            detail="maintenance_pending",
            headers={"Retry-After": "5"},
        )
