"""Versioned MESA HTTP contracts.

``mesa_api.v4_router`` exposes the breaking full-cognitive catalog, immutable
session, mutation, provenance, search, replay and rollback contract.
"""

from .v4_router import create_v4_router

__all__ = ["create_v4_router"]
