"""Add scheduler-supporting columns to ``schedules`` table.

Revision ID: 2026_04_26_0004_sched_cols
Revises: 2026_04_15_0003_pw_reset
Create Date: 2026-04-26

Three additive columns to support the new ``z4j-scheduler``
package per ``docs/SCHEDULER.md §10.2``:

- ``catch_up`` - per-schedule catch-up policy
  (``skip`` | ``fire_one_missed`` | ``fire_all_missed``).
  Default ``skip`` preserves existing celery-beat behavior.
- ``source`` - which surface created this schedule
  (``dashboard`` | ``declarative`` | ``imported_celerybeat`` | ...).
  Default ``dashboard`` for back-compat with existing rows.
- ``source_hash`` - content hash for declarative reconciliation
  (Django/Flask/FastAPI ``Z4J["schedules"]`` declarative diff).
- ``last_fire_id`` - UUID of the most recent fire for idempotent
  correlation between scheduler-side fires and brain-side commands.

Plus one supporting index on ``(scheduler, next_run_at) WHERE
is_enabled`` so the scheduler's "what's due next" query stays
sub-millisecond at 10k+ schedule scale.

This migration is BACKWARDS-COMPATIBLE - existing celery-beat
deployments are unaffected; rows continue to default to
``catch_up='skip'`` (which is what celery-beat does anyway -
missed fires are dropped), ``source='dashboard'`` (preserves
existing dashboard-managed-or-API-managed semantics), and
``source_hash``/``last_fire_id`` remain NULL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2026_04_26_0004_sched_cols"
down_revision: str | Sequence[str] | None = "2026_04_15_0003_pw_reset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(bind, table: str, column: str) -> bool:
    cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
    return column in cols


def _has_index(bind, table: str, index: str) -> bool:
    indexes = {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}
    return index in indexes


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # Defensive: only add columns that don't already exist. Lets
    # this migration be safe to re-run against partially-migrated
    # DBs and against fresh installs where ``Base.metadata.create_all``
    # already added them.

    if not _has_column(bind, "schedules", "catch_up"):
        op.add_column(
            "schedules",
            sa.Column(
                "catch_up",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'skip'"),
            ),
        )
        if is_postgres:
            op.create_check_constraint(
                "schedules_catch_up_valid",
                "schedules",
                "catch_up IN ('skip', 'fire_one_missed', 'fire_all_missed')",
            )

    if not _has_column(bind, "schedules", "source"):
        op.add_column(
            "schedules",
            sa.Column(
                "source",
                sa.String(length=64),
                nullable=False,
                server_default=sa.text("'dashboard'"),
            ),
        )
        if is_postgres:
            op.create_check_constraint(
                "schedules_source_valid",
                "schedules",
                (
                    "source IN ('dashboard', 'declarative', "
                    "'imported_celerybeat', 'imported_rq', "
                    "'imported_apscheduler', 'imported_cron')"
                ),
            )

    if not _has_column(bind, "schedules", "source_hash"):
        op.add_column(
            "schedules",
            sa.Column("source_hash", sa.String(length=128), nullable=True),
        )

    if not _has_column(bind, "schedules", "last_fire_id"):
        last_fire_id_type = (
            sa.dialects.postgresql.UUID(as_uuid=True)
            if is_postgres
            else sa.String(36)
        )
        op.add_column(
            "schedules",
            sa.Column("last_fire_id", last_fire_id_type, nullable=True),
        )

    # Index supports the scheduler's "what's due next" hot-path
    # query: WHERE scheduler = 'z4j-scheduler' AND is_enabled
    # ORDER BY next_run_at LIMIT N. Partial index keeps it small.
    if not _has_index(bind, "schedules", "ix_schedules_scheduler_next_run"):
        if is_postgres:
            op.create_index(
                "ix_schedules_scheduler_next_run",
                "schedules",
                ["scheduler", "next_run_at"],
                postgresql_where=sa.text("is_enabled"),
            )
        else:
            # SQLite: partial indexes work but the syntax differs.
            # Stay simple - a full index here is fine at SQLite-mode
            # scale (single-tenant evaluation deployments).
            op.create_index(
                "ix_schedules_scheduler_next_run",
                "schedules",
                ["scheduler", "next_run_at"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if _has_index(bind, "schedules", "ix_schedules_scheduler_next_run"):
        op.drop_index(
            "ix_schedules_scheduler_next_run",
            table_name="schedules",
        )

    if is_postgres:
        # Drop check constraints first (Postgres requires).
        op.execute(
            "ALTER TABLE schedules DROP CONSTRAINT IF EXISTS schedules_catch_up_valid",
        )
        op.execute(
            "ALTER TABLE schedules DROP CONSTRAINT IF EXISTS schedules_source_valid",
        )
        for col in ("last_fire_id", "source_hash", "source", "catch_up"):
            if _has_column(bind, "schedules", col):
                op.drop_column("schedules", col)
    else:
        # SQLite: ``ALTER TABLE ... DROP COLUMN`` does a full table
        # rebuild and re-evaluates every remaining constraint and
        # default expression. If anything in the schedules table's
        # generated/computed/check definitions still references one
        # of the columns we're about to drop, the rebuild fails with
        # ``no such column: <name>``. ``batch_alter_table`` performs
        # all column drops in one rebuild so the engine sees the
        # consistent target schema and never re-evaluates a
        # half-mutated state. Required for the test_migration
        # downgrade-roundtrip suite (and any operator who deploys to
        # SQLite for eval and needs to roll back).
        with op.batch_alter_table("schedules") as batch_op:
            for col in ("last_fire_id", "source_hash", "source", "catch_up"):
                if _has_column(bind, "schedules", col):
                    batch_op.drop_column(col)
