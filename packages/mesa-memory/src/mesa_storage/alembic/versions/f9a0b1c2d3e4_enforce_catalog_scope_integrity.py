"""Enforce that catalog datasets cannot cross tenant/workspace boundaries."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, Sequence[str], None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    invalid = bind.execute(
        sa.text(
            "SELECT 1 FROM datasets d JOIN workspaces w "
            "ON w.workspace_id = d.workspace_id "
            "WHERE d.tenant_id != w.tenant_id LIMIT 1"
        )
    ).scalar()
    if invalid:
        raise RuntimeError("catalog contains cross-tenant dataset/workspace rows")

    op.execute(
        "CREATE TRIGGER trg_datasets_tenant_workspace_insert "
        "BEFORE INSERT ON datasets FOR EACH ROW "
        "WHEN NOT EXISTS (SELECT 1 FROM workspaces w "
        "WHERE w.workspace_id = NEW.workspace_id AND w.tenant_id = NEW.tenant_id) "
        "BEGIN SELECT RAISE(ABORT, 'dataset workspace must belong to tenant'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_datasets_tenant_workspace_update "
        "BEFORE UPDATE OF tenant_id, workspace_id ON datasets FOR EACH ROW "
        "WHEN NOT EXISTS (SELECT 1 FROM workspaces w "
        "WHERE w.workspace_id = NEW.workspace_id AND w.tenant_id = NEW.tenant_id) "
        "BEGIN SELECT RAISE(ABORT, 'dataset workspace must belong to tenant'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_datasets_tenant_workspace_update")
    op.execute("DROP TRIGGER IF EXISTS trg_datasets_tenant_workspace_insert")
