"""Add ``schedules_changed`` LISTEN/NOTIFY trigger for the scheduler watch.

Revision ID: 2026_04_27_0007_sched_notify
Revises: 2026_04_27_0006_pending_fires
Create Date: 2026-04-27

The brain-side ``WatchSchedules`` gRPC handler used to poll
``schedules.updated_at`` every 2s to compute diffs. That works at
target scale (10k schedules) but burns CPU and adds up to 2s of
cache-freshness latency for the scheduler.

Phase 3 swap-in: a row-level trigger emits ``pg_notify`` on every
INSERT/UPDATE/DELETE. The handler keeps a dedicated asyncpg
connection LISTEN'ing on the channel and emits gRPC events as
they arrive - sub-100ms cache freshness, near-zero idle CPU.

Postgres only. SQLite has no LISTEN/NOTIFY so the handler keeps
the polling path on SQLite.

The trigger payload is a JSON string with the operation kind
(``insert`` / ``update`` / ``delete``) plus the row id and
project_id. Anything else the handler needs (the full row) gets
fetched on demand via the existing repository - we keep the
NOTIFY payload small to stay under the 8KiB Postgres limit even
on big batched updates.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2026_04_27_0007_sched_notify"
down_revision: str | Sequence[str] | None = "2026_04_27_0006_pending_fires"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TRIGGER_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION z4j_schedules_notify() RETURNS trigger AS $$
DECLARE
    payload TEXT;
    row_id UUID;
    proj_id UUID;
    op_name TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        op_name := 'delete';
        row_id := OLD.id;
        proj_id := OLD.project_id;
    ELSIF TG_OP = 'INSERT' THEN
        op_name := 'insert';
        row_id := NEW.id;
        proj_id := NEW.project_id;
    ELSE
        op_name := 'update';
        row_id := NEW.id;
        proj_id := NEW.project_id;
    END IF;
    payload := json_build_object(
        'op', op_name,
        'id', row_id,
        'project_id', proj_id
    )::TEXT;
    PERFORM pg_notify('z4j_schedules_changed', payload);
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
"""


_DROP_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS z4j_schedules_notify_trigger ON schedules;
"""


_DROP_FUNCTION_SQL = """
DROP FUNCTION IF EXISTS z4j_schedules_notify();
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite: skip. The handler detects the dialect at runtime
        # and falls back to polling.
        return
    op.execute(sa.text(_TRIGGER_FUNCTION_SQL))
    op.execute(
        sa.text(
            "CREATE TRIGGER z4j_schedules_notify_trigger "
            "AFTER INSERT OR UPDATE OR DELETE ON schedules "
            "FOR EACH ROW EXECUTE FUNCTION z4j_schedules_notify()",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(sa.text(_DROP_TRIGGER_SQL))
    op.execute(sa.text(_DROP_FUNCTION_SQL))
