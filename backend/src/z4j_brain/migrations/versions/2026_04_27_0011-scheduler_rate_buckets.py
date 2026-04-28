"""Add ``scheduler_rate_buckets`` table for FireSchedule rate limiting.

Revision ID: 2026_04_27_0011_sched_rate
Revises: 2026_04_27_0010_pf_exp_def
Create Date: 2026-04-27

Audit fix (Apr 2026 security audit, follow-up to the FireSchedule
DoS deferral). Per-cert token-bucket state - see
``z4j_brain/domain/scheduler_rate_limiter.py`` for the algorithm and
``z4j_brain/persistence/models/scheduler_rate_bucket.py`` for the
ORM model.

The table is small (one row per scheduler cert; a fleet of 100
schedulers is 100 rows) and the access pattern is "lookup by primary
key, take row lock, recompute, write back" - well within Postgres
single-row contention limits.

Backwards compatible: existing brains without
``scheduler_grpc_fire_rate_limit_enabled`` set to True can stay on
the old schema; the migration adds an empty table that the
application populates on first observation.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2026_04_27_0011_sched_rate"
down_revision: str | Sequence[str] | None = "2026_04_27_0010_pf_exp_def"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    # Defensive: only create when missing. Lets this migration run
    # safely against test fixtures that bootstrap via
    # ``Base.metadata.create_all``.
    if _has_table(bind, "scheduler_rate_buckets"):
        return

    op.create_table(
        "scheduler_rate_buckets",
        sa.Column("cert_cn", sa.String(255), primary_key=True),
        sa.Column("tokens", sa.Float(), nullable=False),
        sa.Column(
            "last_refill",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("capacity", sa.Float(), nullable=False),
        sa.Column("refill_per_second", sa.Float(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "scheduler_rate_buckets"):
        op.drop_table("scheduler_rate_buckets")
