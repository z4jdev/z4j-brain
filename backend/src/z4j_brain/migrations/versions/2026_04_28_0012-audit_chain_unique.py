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
``AuditService.acquire_chain_lock`` is now defense-in-depth: it
serialises the read-then-insert window so the index almost never
trips, and when it does the IntegrityError is the visible signal
of a real bug rather than silent corruption.

The index is PARTIAL (``WHERE prev_row_hmac IS NOT NULL``) because
the genesis row (the first audit row ever written) carries
``prev_row_hmac=NULL`` and Postgres treats NULL != NULL in UNIQUE,
which is the wanted behaviour: a fresh DB has exactly one genesis
row, so this is fine.

CONCURRENTLY on Postgres so an existing populated audit_log table
doesn't take ACCESS EXCLUSIVE during the index build.

PRE-FLIGHT CHECK (added in 1.1.1 after the 1.1.0 incident on
tasks.jfk.work): if existing rows already share a non-NULL
prev_row_hmac, the index cannot be built and the migration would
crash mid-flight with sqlite3.IntegrityError, leaving the DB in
an opaque half-state (alembic version row may be bumped but the
DDL did not run). We refuse cleanly with a precise remediation
message instead. The operator runs ``z4j audit fork-cleanup`` to
quarantine fork rows to ``audit_log_legacy_forks`` (preserves
every byte for forensic review), then re-runs the migration.

This is the Django-grade migration contract: never crash on
real production data; always offer a documented, scripted
remediation; never silently mutate audit data without consent.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.util import CommandError

revision: str = "2026_04_28_0012_audit_unique"
down_revision: str | Sequence[str] | None = "2026_04_27_0011_sched_rate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_INDEX_NAME = "ux_audit_log_prev_row_hmac"


def _check_no_chain_forks(bind: sa.engine.Connection) -> None:
    """Refuse cleanly if existing rows would block the UNIQUE index.

    Pre-flight runs as a SELECT against the live DB. If duplicate
    non-NULL ``prev_row_hmac`` values exist, the partial UNIQUE
    index cannot be created. Rather than letting CREATE INDEX
    raise IntegrityError mid-migration (opaque to operators,
    leaves alembic in a half-applied state), we raise CommandError
    with the exact remediation: a CLI command that quarantines the
    forks and a docs URL with the manual SQL alternative.
    """
    rows = list(
        bind.execute(
            sa.text(
                "SELECT prev_row_hmac, COUNT(*) AS cnt "
                "FROM audit_log "
                "WHERE prev_row_hmac IS NOT NULL "
                "GROUP BY prev_row_hmac "
                "HAVING COUNT(*) > 1"
            )
        ).fetchall()
    )
    if not rows:
        return

    fork_rows = sum(int(r[1]) for r in rows)
    fork_groups = len(rows)
    raise CommandError(
        "\n"
        f"  Cannot apply migration {revision}: audit_log has {fork_rows} rows\n"
        f"  in {fork_groups} duplicate prev_row_hmac groups. The UNIQUE index\n"
        f"  enforces 'one row per chain link' and existing data violates it.\n\n"
        "  This is NOT corruption of your data. It indicates that older z4j\n"
        "  releases (pre-1.1.0) had a chain-fork race the new index closes.\n"
        "  Resolve the forks before re-running the migration:\n\n"
        "    z4j audit fork-cleanup            # interactive, with auto-backup\n"
        "    z4j audit fork-cleanup --apply    # non-interactive (CI/scripts)\n\n"
        "  The cleanup quarantines fork rows to ``audit_log_legacy_forks``\n"
        "  (preserves every byte for forensic review) and keeps the earliest\n"
        "  row per group as the canonical chain link. Then run ``z4j serve``\n"
        "  again to retry the migration.\n\n"
        "  Manual SQL alternative + full background:\n"
        "    https://z4j.dev/operations/upgrade-to-1-1/\n"
    )


def upgrade() -> None:
    bind = op.get_bind()
    _check_no_chain_forks(bind)

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
