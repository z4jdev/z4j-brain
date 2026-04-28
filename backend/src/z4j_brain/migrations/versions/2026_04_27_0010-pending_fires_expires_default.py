"""Add ``server_default`` to ``pending_fires.expires_at``.

Revision ID: 2026_04_27_0010_pf_exp_def
Revises: 2026_04_27_0009_deliv_trig_by
Create Date: 2026-04-27

Audit finding from the v1.1.0 enterprise-grade pass:
``pending_fires.expires_at`` is ``NOT NULL`` but migration 0006
shipped without a ``server_default``. The application path through
``PendingFiresReplayWorker`` always computes and sets the value at
INSERT time, so this is a latent rather than active bug — but a
future code path that builds a ``PendingFire`` row via
``Defaults`` (e.g. raw SQL, or a refactored repository helper that
forgets the field) would IntegrityError instead of falling back to
a sensible default.

Per ``docs/MIGRATIONS.md`` rule #1 (every NOT NULL column needs a
``server_default``), backfill the constraint:

- Postgres: ``CURRENT_TIMESTAMP + interval '7 days'`` matches the
  Python-side default in ``pending_fires_replay_buffer_ttl_seconds``
  (the operator-tunable knob defaults to 7 days, range 1 — 365).
- SQLite: ``DATETIME('now', '+7 days')`` is the equivalent.

This is purely defensive — no in-the-wild code path goes through
the missing-default branch today, so the migration touches column
metadata only and writes no rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2026_04_27_0010_pf_exp_def"
down_revision: str | Sequence[str] | None = "2026_04_27_0009_deliv_trig_by"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres(bind) -> bool:
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    bind = op.get_bind()
    if _is_postgres(bind):
        op.alter_column(
            "pending_fires",
            "expires_at",
            server_default=sa.text(
                "CURRENT_TIMESTAMP + interval '7 days'",
            ),
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
    else:
        # SQLite: alter_column with server_default would trigger
        # batch alter. Use the batch context so the recreate
        # picks up the new default cleanly.
        with op.batch_alter_table("pending_fires") as batch_op:
            batch_op.alter_column(
                "expires_at",
                server_default=sa.text("DATETIME('now', '+7 days')"),
                existing_type=sa.DateTime(timezone=True),
                existing_nullable=False,
            )


def downgrade() -> None:
    bind = op.get_bind()
    if _is_postgres(bind):
        op.alter_column(
            "pending_fires",
            "expires_at",
            server_default=None,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
    else:
        with op.batch_alter_table("pending_fires") as batch_op:
            batch_op.alter_column(
                "expires_at",
                server_default=None,
                existing_type=sa.DateTime(timezone=True),
                existing_nullable=False,
            )
