"""Workflows (M5): DAG definitions with versions, workflow runs, workflow schedules.

Revision ID: 0007_workflows
Revises: 0006_schedules
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_workflows"
down_revision: str | None = "0006_schedules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TRIGGERS = "trigger IN ('manual', 'api', 'schedule', 'retry', 'rerun')"
NEW_TRIGGERS = "trigger IN ('manual', 'api', 'schedule', 'retry', 'rerun', 'workflow')"


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(63), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflows")),
        sa.UniqueConstraint("name", name=op.f("uq_workflows_name")),
    )
    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name=op.f("fk_workflow_versions_workflow_id_workflows"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_versions")),
        sa.UniqueConstraint(
            "workflow_id", "version", name=op.f("uq_workflow_versions_workflow_id_version")
        ),
    )
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_version_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("rerun_of", sa.Uuid(), nullable=True),
        sa.Column("schedule_id", sa.Uuid(), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tasks", postgresql.JSONB(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name=op.f("ck_workflow_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name=op.f("fk_workflow_runs_workflow_id_workflows"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_version_id"],
            ["workflow_versions.id"],
            name=op.f("fk_workflow_runs_workflow_version_id_workflow_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["rerun_of"],
            ["workflow_runs.id"],
            name=op.f("fk_workflow_runs_rerun_of_workflow_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["schedules.id"],
            name=op.f("fk_workflow_runs_schedule_id_schedules"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_runs")),
        sa.UniqueConstraint(
            "schedule_id", "scheduled_for", name=op.f("uq_workflow_runs_schedule_id_scheduled_for")
        ),
    )
    op.create_index(
        "ix_workflow_runs_running",
        "workflow_runs",
        ["updated_at"],
        postgresql_where="status = 'RUNNING'",
    )
    op.create_index(
        "ix_workflow_runs_workflow_created", "workflow_runs", ["workflow_id", "created_at"]
    )

    op.add_column("runs", sa.Column("workflow_run_id", sa.Uuid(), nullable=True))
    op.add_column("runs", sa.Column("task_key", sa.String(63), nullable=True))
    op.create_foreign_key(
        op.f("fk_runs_workflow_run_id_workflow_runs"),
        "runs",
        "workflow_runs",
        ["workflow_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_runs_workflow_run",
        "runs",
        ["workflow_run_id"],
        postgresql_where="workflow_run_id IS NOT NULL",
    )
    op.drop_constraint(op.f("ck_runs_trigger"), "runs", type_="check")
    op.create_check_constraint(op.f("ck_runs_trigger"), "runs", NEW_TRIGGERS)

    op.alter_column("schedules", "job_id", nullable=True)
    op.add_column("schedules", sa.Column("workflow_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_schedules_workflow_id_workflows"),
        "schedules",
        "workflows",
        ["workflow_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        op.f("ck_schedules_one_target"), "schedules", "(job_id IS NULL) <> (workflow_id IS NULL)"
    )


def downgrade() -> None:
    op.execute("DELETE FROM schedules WHERE workflow_id IS NOT NULL")
    op.drop_constraint(op.f("ck_schedules_one_target"), "schedules", type_="check")
    op.drop_constraint(op.f("fk_schedules_workflow_id_workflows"), "schedules", type_="foreignkey")
    op.drop_column("schedules", "workflow_id")
    op.alter_column("schedules", "job_id", nullable=False)
    op.execute("UPDATE runs SET trigger = 'manual' WHERE trigger = 'workflow'")
    op.drop_constraint(op.f("ck_runs_trigger"), "runs", type_="check")
    op.create_check_constraint(op.f("ck_runs_trigger"), "runs", OLD_TRIGGERS)
    op.drop_index(
        "ix_runs_workflow_run", table_name="runs", postgresql_where="workflow_run_id IS NOT NULL"
    )
    op.drop_constraint(op.f("fk_runs_workflow_run_id_workflow_runs"), "runs", type_="foreignkey")
    op.drop_column("runs", "task_key")
    op.drop_column("runs", "workflow_run_id")
    op.drop_index("ix_workflow_runs_workflow_created", table_name="workflow_runs")
    op.drop_index(
        "ix_workflow_runs_running",
        table_name="workflow_runs",
        postgresql_where="status = 'RUNNING'",
    )
    op.drop_table("workflow_runs")
    op.drop_table("workflow_versions")
    op.drop_table("workflows")
