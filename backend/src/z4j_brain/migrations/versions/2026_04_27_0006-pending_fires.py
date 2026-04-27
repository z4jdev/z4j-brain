"""Add ``pending_fires`` table for the z4j-scheduler buffered-fire feature.

Revision ID: 2026_04_27_0006_pending_fires
Revises: 2026_04_26_0005_deliv_snap
Create Date: 2026-04-27

When the z4j-scheduler asks brain to fire a schedule and no agent
is online for the schedule's engine, brain stores the fire in this
table instead of returning ``agent_offline``. A background worker
replays buffered fires the moment a matching agent comes online,
honouring the schedule's ``catch_up`` policy:

- ``skip``: the fire is buffered briefly but the worker drops it
  rather than replaying. The buffer here is mostly a transient
  observability surface ("we noticed an outage").
- ``fire_one_missed``: the worker replays only the most recent
  buffered fire per schedule.
- ``fire_all_missed``: the worker replays every buffered fire in
  ``scheduled_for`` order so the agent gets the full backlog.

Per ``docs/SCHEDULER.md §11`` (Phase 2). Backwards compatible -
existing brains without z4j-scheduler attached never write rows
to this table.

Schema notes:

- ``fire_id`` carries the scheduler's idempotency key
  (``uuid5(NAMESPACE, schedule_id + scheduled_for_iso)``) so a
  retry of FireSchedule from the scheduler doesn't duplicate the
  buffer entry.
- ``payload`` is the same dict ``CommandDispatcher.issue`` would
  receive at fire time. Serialised here so replay reproduces the
  exact original intent without re-deriving from the schedule
  (which may have been edited in the meantime).
- ``expires_at`` defaults to ``enqueued_at + 7 days`` so a long
  agent outage doesn't accumulate indefinitely. Operators tune via
  ``Z4J_PENDING_FIRES_RETENTION_DAYS``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2026_04_27_0006_pending_fires"
down_revision: str | Sequence[str] | None = "2026_04_26_0005_deliv_snap"
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

    # Defensive: only create the table when missing. Lets this
    # migration run safely against test fixtures that bootstrap via
    # ``Base.metadata.create_all`` (which already materialises
    # ``pending_fires`` from the ORM model).
    if _has_table(bind, "pending_fires"):
        # Indexes still need the existence check because create_all
        # may emit them with slightly different names depending on
        # SQLAlchemy version.
        if not _has_index(bind, "pending_fires", "ix_pending_fires_replay"):
            op.create_index(
                "ix_pending_fires_replay",
                "pending_fires",
                ["project_id", "engine", "scheduled_for"],
            )
        if not _has_index(bind, "pending_fires", "ix_pending_fires_expires"):
            op.create_index(
                "ix_pending_fires_expires",
                "pending_fires",
                ["expires_at"],
            )
        return

    uuid_type = (
        sa.dialects.postgresql.UUID(as_uuid=True)
        if is_postgres
        else sa.String(36)
    )
    jsonb_type = (
        sa.dialects.postgresql.JSONB
        if is_postgres
        else sa.JSON
    )

    op.create_table(
        "pending_fires",
        sa.Column(
            "id",
            uuid_type,
            primary_key=True,
            # Postgres uses gen_random_uuid via pgcrypto; SQLite has no
            # equivalent so the application supplies the UUID.
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
        sa.Column("engine", sa.String(40), nullable=False),
        sa.Column("payload", jsonb_type(), nullable=False),
        sa.Column(
            "scheduled_for",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "enqueued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )

    # Hot-path index for the replay worker: "buffered fires for this
    # project + engine, oldest first." Keep separate from the unique
    # constraint on fire_id so the worker query never touches the
    # uniqueness index.
    op.create_index(
        "ix_pending_fires_replay",
        "pending_fires",
        ["project_id", "engine", "scheduled_for"],
    )

    # Sweep index for the expiry path - simple BTree on expires_at
    # so the cleanup worker can find expired rows in log time even
    # at very large buffer sizes.
    op.create_index(
        "ix_pending_fires_expires",
        "pending_fires",
        ["expires_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "pending_fires"):
        return
    if _has_index(bind, "pending_fires", "ix_pending_fires_expires"):
        op.drop_index("ix_pending_fires_expires", table_name="pending_fires")
    if _has_index(bind, "pending_fires", "ix_pending_fires_replay"):
        op.drop_index("ix_pending_fires_replay", table_name="pending_fires")
    op.drop_table("pending_fires")
