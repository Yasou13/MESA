"""add_mcp_activity_approval

Revision ID: 087de6628c51
Revises: 9c6c7ae69ed7
Create Date: 2026-07-24 22:49:14.706830

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "087de6628c51"
down_revision: Union[str, Sequence[str], None] = "9c6c7ae69ed7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "mcp_tool_calls",
        sa.Column("call_id", sa.String(), nullable=False),
        sa.Column("trace_id", sa.String(), nullable=False),
        sa.Column("connection_id", sa.String(), nullable=True),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("principal_id", sa.String(), nullable=True),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.Column("workspace_id", sa.String(), nullable=True),
        sa.Column("dataset_id", sa.String(), nullable=True),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("operation_type", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("request_size_bytes", sa.Integer(), nullable=True),
        sa.Column("response_size_bytes", sa.Integer(), nullable=True),
        sa.Column("request_summary", sa.String(), nullable=True),
        sa.Column("request_fingerprint", sa.String(), nullable=True),
        sa.Column("memory_id", sa.String(), nullable=True),
        sa.Column("mutation_id", sa.String(), nullable=True),
        sa.Column("pipeline_run_id", sa.String(), nullable=True),
        sa.Column("vector_status", sa.String(), nullable=True),
        sa.Column("graph_status", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("metadata_json", sa.String(), nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("call_id"),
    )

    op.create_table(
        "mcp_approval_requests",
        sa.Column("approval_id", sa.String(), nullable=False),
        sa.Column("call_id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("request_summary", sa.String(), nullable=False),
        sa.Column("payload_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("payload_hash", sa.String(), nullable=False),
        sa.Column("requested_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=True),
        sa.Column("decided_at", sa.String(), nullable=True),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("decision_reason", sa.String(), nullable=True),
        sa.Column("execution_status", sa.String(), nullable=True),
        sa.Column("executed_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("approval_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("mcp_approval_requests")
    op.drop_table("mcp_tool_calls")
