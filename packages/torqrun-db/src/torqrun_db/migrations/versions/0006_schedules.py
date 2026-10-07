"""Schedules (M4): cron and interval schedules, scheduled runs unique per slot.

Revision ID: 0006_schedules
Revises: 0005_remote_agents
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_schedules"
down_revision: str | None = "0005_remote_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("cron", sa.String(120), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("misfire_policy", sa.String(10), nullable=False),
        sa.Column("misfire_grace_seconds", sa.Integer(), nullable=False),
        sa.Column("max_catchup", sa.Integer(), nullable=False),
        sa.Column("overlap_policy", sa.String(10), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_fire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_id", sa.Uuid(), nullable=True),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("last_skipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_skip_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('cron', 'interval')", name=op.f("ck_schedules_kind")),
        sa.CheckConstraint(
            "misfire_policy IN ('skip', 'run_once', 'run_all')", name=op.f("ck_schedules_misfire")
        ),
        sa.CheckConstraint(
            "overlap_policy IN ('allow', 'skip')", name=op.f("ck_schedules_overlap")
        ),
        sa.CheckConstraint(
            "(kind = 'cron' AND cron IS NOT NULL) OR (kind = 'interval' AND interval_seconds > 0)",
            name=op.f("ck_schedules_definition"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_schedules_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedules")),
    )
    op.create_index("ix_schedules_due", "schedules", ["next_fire_at"], postgresql_where="enabled")
    op.add_column("runs", sa.Column("schedule_id", sa.Uuid(), nullable=True))
    op.add_column("runs", sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_runs_schedule_id_schedules"),
        "runs",
        "schedules",
        ["schedule_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        op.f("uq_runs_schedule_id_scheduled_for"), "runs", ["schedule_id", "scheduled_for"]
    )
    op.create_foreign_key(
        op.f("fk_schedules_last_run_id_runs"),
        "schedules",
        "runs",
        ["last_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_schedules_last_run_id_runs"), "schedules", type_="foreignkey")
    op.drop_constraint(op.f("uq_runs_schedule_id_scheduled_for"), "runs", type_="unique")
    op.drop_constraint(op.f("fk_runs_schedule_id_schedules"), "runs", type_="foreignkey")
    op.drop_column("runs", "scheduled_for")
    op.drop_column("runs", "schedule_id")
    op.drop_index("ix_schedules_due", table_name="schedules", postgresql_where="enabled")
    op.drop_table("schedules")
