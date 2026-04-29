"""Create ``agent_workers`` table for the worker-first protocol (1.2.1+).

Revision ID: 2026_04_29_0013_agent_workers
Revises: 2026_04_28_0012_audit_unique
Create Date: 2026-04-29

The 1.2.0 worker-first protocol let multiple worker processes
under one agent_id register simultaneously (one slot per
worker_id). 1.2.0 tracked them in the in-memory registry only;
1.2.1 persists each worker as a row in ``agent_workers`` so the
brain has durable state for introspection, dashboards, and
audit.

This is DISTINCT from the existing ``workers`` table which
tracks engine-native workers (Celery / RQ / Dramatiq processes
known to their broker via heartbeat events). A gunicorn web
worker is an ``agent_worker`` but not a ``worker``; a Celery
worker process is both.

Additive migration: no existing tables touched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "2026_04_29_0013_agent_workers"
down_revision: str | Sequence[str] | None = "2026_04_28_0012_audit_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    # Defensive: skip if the test fixture's
    # ``Base.metadata.create_all`` already produced the table on
    # a fresh DB. This pattern matches 2026_04_27_0011_sched_rate
    # and earlier additive migrations.
    if _has_table(bind, "agent_workers"):
        return

    op.create_table(
        "agent_workers",
        # PKMixin: id (UUID), TimestampsMixin: created_at, updated_at
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "agent_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("role", sa.String(32), nullable=True),
        sa.Column("framework", sa.String(40), nullable=True),
        sa.Column("pid", sa.Integer, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "state",
            sa.String(20),
            nullable=False,
            server_default="online",
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_connect_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "agent_id", "worker_id", name="uq_agent_workers_agent_worker",
        ),
    )
    op.create_index(
        "ix_agent_workers_project_state",
        "agent_workers",
        ["project_id", "state"],
    )
    op.create_index(
        "ix_agent_workers_agent_state",
        "agent_workers",
        ["agent_id", "state"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_workers_agent_state", table_name="agent_workers")
    op.drop_index("ix_agent_workers_project_state", table_name="agent_workers")
    op.drop_table("agent_workers")
