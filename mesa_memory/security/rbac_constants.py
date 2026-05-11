"""RBAC identity constants for MESA storage layer.

Defines the reserved system daemon identity and the fail-closed sentinel
used when callers omit credentials.  All storage write methods default to
``_UNSET_IDENTITY`` — any call that doesn't explicitly pass an agent_id /
session_id will fail the RBAC check with a ``PermissionError``.

Internal daemons (``ConsolidationLoop``, ``StorageFacade.reconcile_orphans``,
``StorageFacade.soft_delete_all``) must pass ``SYSTEM_AGENT_ID`` /
``SYSTEM_SESSION_ID`` to authenticate as the system process.
"""

# ---------------------------------------------------------------------------
# System daemon identity — seeded in AccessControl._init_db() with WRITE
# ---------------------------------------------------------------------------
SYSTEM_AGENT_ID: str = "__mesa_system__"
SYSTEM_SESSION_ID: str = "__mesa_system__"

# ---------------------------------------------------------------------------
# Fail-closed sentinel — has NO permissions in the RBAC database, so any
# check against it will return False → PermissionError.
# ---------------------------------------------------------------------------
_UNSET_IDENTITY: str = "__unset__"
