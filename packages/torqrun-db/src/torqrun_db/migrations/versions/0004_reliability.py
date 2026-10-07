"""Reliability (M2): retry scheduling, reruns, queue settings, idempotency keys, API liveness.

Revision ID: 0004_reliability
Revises: 0003_agent_system_stats
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_reliability"
down_revision: str | None = "0003_agent_system_stats"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("rerun_of", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_runs_rerun_of_runs"), "runs", "runs", ["rerun_of"], ["id"], ondelete="SET NULL"
    )
    op.create_index(
        "ix_runs_retry_due", "runs", ["next_attempt_at"], postgresql_where="status = 'RETRY_WAIT'"
    )
    op.create_table(
        "queues",
        sa.Column("name", sa.String(length=63), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=True),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_queues")),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("scope", sa.String(length=120), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("scope", "key", name=op.f("pk_idempotency_keys")),
    )
    op.create_index("ix_idempotency_keys_created", "idempotency_keys", ["created_at"])
    op.create_table(
        "control_plane_heartbeats",
        sa.Column("replica_id", sa.String(length=64), nullable=False),
        sa.Column("up_since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_alive_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("replica_id", name=op.f("pk_control_plane_heartbeats")),
    )


def downgrade() -> None:
    op.drop_table("control_plane_heartbeats")
    op.drop_index("ix_idempotency_keys_created", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_table("queues")
    op.drop_index("ix_runs_retry_due", table_name="runs", postgresql_where="status = 'RETRY_WAIT'")
    op.drop_constraint(op.f("fk_runs_rerun_of_runs"), "runs", type_="foreignkey")
    op.drop_column("runs", "rerun_of")
    op.drop_column("runs", "next_attempt_at")
