"""Capability-scoped adapters over the transitional storage kernel.

These adapters prevent new consumers from acquiring the entire legacy DAO
surface. SQL and backend ownership remain inside ``StorageKernel`` while the
0.8 migration proceeds capability by capability.
"""

from __future__ import annotations

from typing import Any, ClassVar

from mesa_storage.storage_kernel import StorageKernel


class _CapabilityModule:
    __slots__ = ("_kernel",)
    storage_capability_version = 1
    operations: ClassVar[frozenset[str]]

    def __init__(self, kernel: StorageKernel) -> None:
        self._kernel = kernel

    def __getattr__(self, name: str) -> Any:
        if name not in self.operations:
            raise AttributeError(
                f"{type(self).__name__} does not expose storage operation {name!r}"
            )
        return getattr(self._kernel, name)


class MutationLedger(_CapabilityModule):
    operations = frozenset(
        {
            "admit_v4_memory",
            "reserve_v4_idempotency",
            "complete_v4_idempotency",
            "record_mutation",
            "get_mutation",
            "get_mutation_summary",
            "list_session_mutation_summaries",
            "transition_pipeline_run",
            "get_pipeline_run",
            "set_mutation_state",
            "record_mutation_tier3_audit",
            "record_mutation_extraction",
            "record_mutation_artifact",
            "request_pipeline_rollback",
            "replay_pipeline_run",
        }
    )


class ProjectionStore(_CapabilityModule):
    operations = frozenset(
        {
            "get_projection_mutation",
            "claim_projection_outbox",
            "complete_projection_outbox",
            "fail_projection_outbox",
            "renew_projection_outbox_lease",
            "claim_artifact_cleanup",
            "apply_artifact_cleanup",
            "finish_artifact_cleanup",
            "reconcile_v4_projection_parity",
            "reconcile_v4_bidirectional",
            "project_v4_sql_entity",
            "project_v4_vector_entity",
            "project_v4_graph_triplet",
            "get_pipeline_run",
        }
    )


class IngestionQueue(_CapabilityModule):
    operations = frozenset(
        {
            "admit_raw_log",
            "get_queue_admission_metrics",
            "insert_raw_log",
            "get_raw_log",
            "get_recent_logs",
            "request_session_finalization",
            "claim_session_finalization",
            "get_session_finalization",
            "list_pending_session_finalizations",
            "get_pending_session_raw_logs",
            "complete_session_finalization",
            "fail_session_finalization",
            "recover_expired_session_finalizations",
            "update_raw_log_status",
            "claim_raw_log",
            "transition_claimed_raw_log",
            "recover_expired_raw_log_claims",
            "dispatch_raw_log",
            "recover_raw_log_dispatches",
            "claim_dispatch_queue",
            "complete_dispatch_queue",
            "renew_dispatch_queue_lease",
            "get_dispatch_completion_receipt",
            "get_dispatch_receipt",
            "get_dispatch_receipt_by_source",
        }
    )


class LegacyMemoryStore(_CapabilityModule):
    operations = frozenset(
        {
            "insert_memory",
            "bulk_insert_memory",
            "search_memory",
            "search_memory_fts",
            "get_memories",
            "get_epistemic_data_for_nodes",
            "get_nodes_by_ids_batch",
            "update_entity_description",
            "mark_consolidated",
            "insert_edge",
            "get_all_edges",
            "get_neighbors",
            "invalidate_node",
            "find_nodes_by_name",
            "find_consolidated_nodes_by_name",
            "get_all_active_agent_ids",
            "get_memory_by_id",
            "get_node_degree",
            "insert_routing_telemetry",
            "get_recent_telemetry_stats",
        }
    )


class PurgeCoordinator(_CapabilityModule):
    operations = frozenset(
        {
            "purge_memory",
            "purge_v4_document",
            "resume_purge",
            "resume_incomplete_purges",
            "rollback_purge",
        }
    )
