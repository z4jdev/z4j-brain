"""Add UNIQUE partial index on ``audit_log.prev_row_hmac``.

Revision ID: 2026_04_28_0012_audit_chain_unique
Revises: 2026_04_27_0011_sched_rate
Create Date: 2026-04-28

Round-9 audit fix R9-Stor-H5 (Apr 2026): the HMAC chain was only
enforced by an advisory lock + sort-order convention. A bug, a
missed lock, the SQLite path, or a future code path that bypasses
``AuditService.record`` could produce two audit rows sharing the
same ``prev_row_hmac=X``. ``verify_chain`` then misclassifies one
as a "deleted row" false positive, the chain stays forked
forever, and there's no way to detect or repair without manual
surgery.

Adding a partial UNIQUE index makes the second writer fail loud
at the DB level (``IntegrityError`` with a clear constraint name)
instead of silently forking. The advisory lock in
``AuditService.acquire_chain_lock`` is now defense-in-depth — it
serialises the read-then-insert window so the index almost never
trips, and when it does the IntegrityError is the visible signal
of a real bug rather than silent corruption.

The index is PARTIAL (``WHERE prev_row_hmac IS NOT NULL``) because
the genesis row (the first audit row ever written) carries
``prev_row_hmac=NULL`` and Postgres treats NULL ≠ NULL in UNIQUE,
which is the wanted behaviour — a fresh DB has exactly one
genesis row, so this is fine.

CONCURRENTLY on Postgres so an existing populated audit_log table
doesn't take ACCESS EXCLUSIVE during the index build. Per the
docs/MIGRATIONS.md §2 contract: additive only.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2026_04_28_0012_audit_unique"
down_revision: str | Sequence[str] | None = "2026_04_27_0011_sched_rate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_INDEX_NAME = "ux_audit_log_prev_row_hmac"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # CONCURRENTLY can't run inside a transaction; env.py sets
        # ``transaction_per_migration=True`` so each migration is
        # in its own tx and ``autocommit_block`` here exits it.
        with op.get_context().autocommit_block():
            op.execute(
                sa.text(
                    f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
                    f"{_INDEX_NAME} "
                    f"ON audit_log (prev_row_hmac) "
                    f"WHERE prev_row_hmac IS NOT NULL",
                ),
            )
    else:
        # SQLite supports partial indexes since 3.8 (we ship 3.40+).
        op.execute(
            sa.text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_NAME} "
                f"ON audit_log (prev_row_hmac) "
                f"WHERE prev_row_hmac IS NOT NULL",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                sa.text(
                    f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}",
                ),
            )
    else:
        op.execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX_NAME}"))
