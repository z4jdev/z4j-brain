"""Add ``schedule_fires`` table for per-fire history.

Revision ID: 2026_04_27_0008_schedule_fires
Revises: 2026_04_27_0007_sched_notify
Create Date: 2026-04-27

Phase 4 of the z4j-scheduler integration. Each fire dispatched by
the scheduler creates one row here so operators can answer
"did the 3am cron fire?" without grepping logs. The row records
when the fire was scheduled, when it actually went out, the
brain-assigned command id, the eventual outcome, and the latency.

Schema:

- ``fire_id`` UUID primary key. Matches the scheduler's
  ``uuid5(NAMESPACE, schedule_id + scheduled_for_iso)`` so a
  retry of FireSchedule from the scheduler doesn't create a
  duplicate row (UNIQUE on fire_id).
- ``command_id`` foreign key to ``commands`` (NULL when the fire
  was buffered or rejected before reaching the dispatcher).
- ``status`` text: ``pending`` / ``delivered`` / ``buffered`` /
  ``acked_success`` / ``acked_failed`` / ``failed``.
- ``scheduled_for`` / ``fired_at`` / ``acked_at`` timestamps.
- ``error_code`` / ``error_message`` for failure debugging.

Indexes:

- ``ix_schedule_fires_schedule_recent`` on ``(schedule_id,
  fired_at DESC)`` so the dashboard's "last 50 fires" page is
  sub-millisecond.
- ``ix_schedule_fires_circuit_breaker`` on ``(schedule_id,
  status, fired_at DESC)`` so the circuit-breaker worker's
  "last N fires for this schedule" scan stays cheap as the table
  grows.

Retention: rows older than ``Z4J_SCHEDULE_FIRES_RETENTION_DAYS``
(default 30) are pruned by a periodic worker. At 10 schedules
firing every minute, that's ~430k rows per month - well within
Postgres single-table comfort.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2026_04_27_0008_schedule_fires"
down_revision: str | Sequence[str] | None = "2026_04_27_0007_sched_notify"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _has_index(bind, table: str, index: str) -> bool:
    if not _has_table(bind, table):
        return False
    return index in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # Defensive: tests bootstrap via Base.metadata.create_all so the
    # table may already exist. Same pattern as 0006-pending_fires.
    if _has_table(bind, "schedule_fires"):
        for idx_name, idx_cols in (
            ("ix_schedule_fires_schedule_recent", ["schedule_id", "fired_at"]),
            (
                "ix_schedule_fires_circuit_breaker",
                ["schedule_id", "status", "fired_at"],
            ),
        ):
            if not _has_index(bind, "schedule_fires", idx_name):
                op.create_index(idx_name, "schedule_fires", idx_cols)
        return

    uuid_type = (
        sa.dialects.postgresql.UUID(as_uuid=True)
        if is_postgres
        else sa.String(36)
    )

    op.create_table(
        "schedule_fires",
        sa.Column(
            "id",
            uuid_type,
            primary_key=True,
            server_default=(
                sa.text("gen_random_uuid()") if is_postgres else None
            ),
        ),
        sa.Column("fire_id", uuid_type, nullable=False, unique=True),
        sa.Column(
            "schedule_id",
            uuid_type,
            sa.ForeignKey("schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            uuid_type,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "command_id",
            uuid_type,
            sa.ForeignKey("commands.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "scheduled_for",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "fired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "acked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(2000), nullable=True),
    )

    # Hot-path indexes.
    op.create_index(
        "ix_schedule_fires_schedule_recent",
        "schedule_fires",
        ["schedule_id", "fired_at"],
    )
    op.create_index(
        "ix_schedule_fires_circuit_breaker",
        "schedule_fires",
        ["schedule_id", "status", "fired_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "schedule_fires"):
        return
    if _has_index(bind, "schedule_fires", "ix_schedule_fires_circuit_breaker"):
        op.drop_index(
            "ix_schedule_fires_circuit_breaker", table_name="schedule_fires",
        )
    if _has_index(bind, "schedule_fires", "ix_schedule_fires_schedule_recent"):
        op.drop_index(
            "ix_schedule_fires_schedule_recent", table_name="schedule_fires",
        )
    op.drop_table("schedule_fires")
