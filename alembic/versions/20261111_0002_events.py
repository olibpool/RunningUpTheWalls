from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261111_0002"
down_revision = "20261110_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "commitment",
        sa.Column("minimal_plan", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("commitment", "minimal_plan")
