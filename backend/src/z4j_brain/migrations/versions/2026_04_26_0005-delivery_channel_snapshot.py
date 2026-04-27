"""Snapshot ``channel_name`` + ``channel_type`` on delivery rows.

Revision ID: 2026_04_26_0005_deliv_snap
Revises: 2026_04_26_0004_sched_cols
Create Date: 2026-04-26

Audit L-2 (added v1.0.14): the dashboard's Delivery Log page resolves
the channel_name + channel_type live from the channels table via a
batch JOIN at read time. That has two problems:

1. **Audit integrity** - if an admin renames ``"#prod-alerts"`` to
   ``"#dev-test"`` after dispatching a sensitive notification, every
   historical delivery row appears to have gone to ``"#dev-test"``.
   A rogue admin can rename a channel to retrofit the audit story.
2. **Channel-deleted blank** - if a channel is deleted, every
   historical delivery for it shows as ``"(channel deleted)"``
   permanently with no way to recover what destination was used.

Fix: snapshot ``channel_name`` and ``channel_type`` into the
``notification_deliveries`` row at INSERT time. Reads use the
snapshotted columns first; the live JOIN remains as a fallback only
for backfilled (NULL) rows from before this migration.

Both columns are NULLable so existing rows don't need backfill -
``list_deliveries`` falls back to the live channel join when the
snapshot columns are NULL on a row. Operators can run a one-shot
backfill SQL if they want full snapshot coverage of historical
rows; the migration doesn't do it because it would lock the table
on a busy install.

Also adds an index on ``project_id`` since we're already touching
the table - existing index on ``(project_id, sent_at, id)`` covers
the keyset paginator but isn't a great fit for the new
DELETE...WHERE project_id pattern from the v1.0.14
``clear_deliveries`` endpoint. (Actually that DELETE already uses
the same composite, so no extra index needed - skipping it.)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2026_04_26_0005_deliv_snap"
down_revision: str | Sequence[str] | None = "2026_04_26_0004_sched_cols"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(bind, table: str, column: str) -> bool:
    cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
    return column in cols


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "notification_deliveries", "channel_name"):
        op.add_column(
            "notification_deliveries",
            sa.Column("channel_name", sa.String(length=200), nullable=True),
        )
    if not _has_column(bind, "notification_deliveries", "channel_type"):
        op.add_column(
            "notification_deliveries",
            sa.Column("channel_type", sa.String(length=20), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "notification_deliveries", "channel_type"):
        op.drop_column("notification_deliveries", "channel_type")
    if _has_column(bind, "notification_deliveries", "channel_name"):
        op.drop_column("notification_deliveries", "channel_name")
