"""Add ``triggered_by_user_id`` to ``notification_deliveries``.

Revision ID: 2026_04_27_0009_deliv_trig_by
Revises: 2026_04_27_0008_schedule_fires
Create Date: 2026-04-27

Bug 1 from the v1.1.0 backlog (deferred from 1.0.19): test fires
dispatched from the project Channels tab don't show up in the
operator's Global Notification Log. Root cause: the personal log
query in ``NotificationDeliveryRepository.list_for_user`` filters by
``subscription_id IN (subs owned by user)``, but a test fire writes
a delivery row with ``subscription_id = NULL`` (test fires aren't
owned by any subscription — they're standalone "test the channel"
actions). So test fires drop out of the personal log.

We can't reuse ``subscription_id`` as the user-owner pointer because
test fires legitimately have no subscription. We need a separate,
explicit pointer to the user who triggered the test.

Fix: add ``triggered_by_user_id`` UUID, NULL-able, ``ON DELETE SET
NULL``. Test endpoints (``test_channel_config`` /
``test_saved_channel``) populate it with ``current_user.id`` at
write time; the personal log query OR's it into the WHERE clause
so a row owned-by-user via either subscription OR triggered_by
surfaces in the Global Notification Log. The dashboard renders
``triggered_by_user_id IS NOT NULL`` rows with a "channel test"
badge so operators can distinguish them from real subscription
fires at a glance.

Per ``docs/MIGRATIONS.md`` rule #1: NULL-able column, no
``server_default`` needed (NULL is a sensible default for every
existing row — they pre-date the feature). Old code that doesn't
SELECT this column keeps working unchanged. Forward-compat for any
v1.1.x patch and back-compat with v1.0.x readers (column is just
ignored).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2026_04_27_0009_deliv_trig_by"
down_revision: str | Sequence[str] | None = "2026_04_27_0008_schedule_fires"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(bind, table: str, column: str) -> bool:
    cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
    return column in cols


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "notification_deliveries", "triggered_by_user_id"):
        op.add_column(
            "notification_deliveries",
            sa.Column(
                "triggered_by_user_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        # Targeted index for the Global Notification Log query path —
        # ``list_for_user`` ORs ``triggered_by_user_id = :uid`` into
        # the WHERE clause and we want it to be a cheap lookup, not
        # a sequential scan, on installs with many test fires.
        #
        # Round-6 audit fix Mig-HIGH-2 (Apr 2026): on Postgres use
        # CREATE INDEX CONCURRENTLY so the upgrade doesn't take an
        # ACCESS EXCLUSIVE lock on an already-populated
        # ``notification_deliveries`` table. CONCURRENTLY cannot run
        # inside a transaction; alembic env.py sets
        # ``transaction_per_migration = True`` so each migration runs
        # in its own tx — we exit it here and run autocommit.
        if bind.dialect.name == "postgresql":
            with op.get_context().autocommit_block():
                op.execute(
                    sa.text(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                        "ix_notification_deliveries_triggered_by_user "
                        "ON notification_deliveries (triggered_by_user_id)",
                    ),
                )
        else:
            op.create_index(
                "ix_notification_deliveries_triggered_by_user",
                "notification_deliveries",
                ["triggered_by_user_id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "notification_deliveries", "triggered_by_user_id"):
        return
    op.drop_index(
        "ix_notification_deliveries_triggered_by_user",
        table_name="notification_deliveries",
    )
    # SQLite cannot DROP COLUMN when there's a foreign-key constraint
    # involving the column - it has to recreate the table. Use the
    # batch context so alembic does the recreate-table dance for us.
    # Postgres handles drop_column natively but batch is harmless
    # there too.
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("notification_deliveries") as batch_op:
            batch_op.drop_column("triggered_by_user_id")
    else:
        op.drop_column("notification_deliveries", "triggered_by_user_id")
