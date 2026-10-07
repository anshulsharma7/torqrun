"""Remote agents (M3): enrollment-token presets, credential rotation, agent capabilities.

Revision ID: 0005_remote_agents
Revises: 0004_reliability
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_remote_agents"
down_revision: str | None = "0004_reliability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "enrollment_tokens", sa.Column("prefix", sa.String(16), nullable=False, server_default="")
    )
    op.add_column(
        "enrollment_tokens",
        sa.Column("queues", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    )
    op.add_column(
        "enrollment_tokens",
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    )
    op.add_column(
        "enrollment_tokens",
        sa.Column("created_by", sa.String(120), nullable=False, server_default=""),
    )
    op.add_column(
        "agent_credentials", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "agents",
        sa.Column(
            "capabilities", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{process}"
        ),
    )


def downgrade() -> None:
    op.drop_column("agents", "capabilities")
    op.drop_column("agent_credentials", "expires_at")
    for col in ("created_by", "tags", "queues", "prefix"):
        op.drop_column("enrollment_tokens", col)
