"""Agents: latest host stats reported with each heartbeat.

Revision ID: 0003_agent_system_stats
Revises: 0002_jobs_runs_agents
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_agent_system_stats"
down_revision: str | None = "0002_jobs_runs_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("system_stats", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "system_stats")
