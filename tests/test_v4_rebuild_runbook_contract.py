"""Executable documentation contracts for the offline V4 rebuild."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_rebuild_runbook_records_exact_offline_control_flow() -> None:
    runbook = (ROOT / "docs" / "v4-rebuild-runbook.md").read_text(encoding="utf-8")

    required_fragments = {
        "MESA_V4_REBUILD_ENABLED=true",
        "MESA_EMBEDDING_VERSION=v1",
        "MESA_EMBEDDING_DIMENSION=1536",
        "mesa-v4-admin grant-control --principal rebuild-operator",
        "/v4/operations/rebuild",
        "/v4/operations/$MESA_REBUILD_OPERATION_ID/retry",
        "/v4/operations/$MESA_REBUILD_OPERATION_ID/cancel",
        "mesa-v4-rebuild run",
        "mesa-v4-rebuild adopt-provider",
        "--confirm-legacy-provider-unknown",
        "--trusted-root /srv/mesa",
        "--storage-root /srv/mesa/v4-data",
        "--work-root /srv/mesa/rebuild-work",
        '--operation-id "$MESA_REBUILD_OPERATION_ID"',
        "RETRYABLE_FAILED",
        "READY_TO_CUTOVER",
        "retained generation",
        "24 saat soak",
        "`NO-GO`",
    }
    assert all(fragment in runbook for fragment in required_fragments)


def test_rebuild_runbook_keeps_scope_and_cleanup_fail_closed() -> None:
    runbook = (ROOT / "docs" / "v4-rebuild-runbook.md").read_text(encoding="utf-8")
    env_template = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "Tenant, workspace veya dataset kapsamlı rebuild desteklenmez" in runbook
    assert "Backup ve retained generation otomatik silinmez" in runbook
    assert "manuel HTTP rollback endpoint'i sunmaz" in runbook
    assert "Dataset-bound MCP" in runbook
    assert "MESA_V4_REBUILD_ENABLED=false" in env_template
    assert "MESA_EMBEDDING_VERSION=v1" in env_template
