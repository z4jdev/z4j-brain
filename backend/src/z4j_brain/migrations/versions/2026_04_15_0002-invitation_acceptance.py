"""Add ``invitations.accepted_by_user_id`` + unique token_hash index.

Revision ID: 2026_04_15_0002_invitation_acceptance
Revises: 2026_04_15_v2_initial
Create Date: 2026-04-15

Both are safe to run on a live database:

- ``accepted_by_user_id`` is nullable with an ``ON DELETE SET NULL``
  FK to ``users.id``, so existing invitation rows become stamped
  lazily on the next accept. There's no backfill to perform for
  v1 because nothing in production has accepted an invitation
  through the old code path.
- The ``ix_invitations_token_hash`` UNIQUE index is created
  unconditionally; the pre-existing, ``IF NOT EXISTS``-guarded
  ALTER that ran on my dev DB matches what this migration emits.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2026_04_15_0002_invite_accept"
down_revision: str | Sequence[str] | None = "2026_04_15_v2_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(bind, table: str, column: str) -> bool:
    """Inspector helper: True iff ``table.column`` already exists.

    Both upgrade paths can hit this migration with the column already
    present - fresh tests run ``Base.metadata.create_all()`` from the
    initial migration which builds the *current* model (already
    contains ``accepted_by_user_id``). Real prod DBs that ran 0001
    before the column was added will not have it. Idempotent guards
    let both paths converge.
    """
    inspector = sa.inspect(bind)
    return any(c["name"] == column for c in inspector.get_columns(table))


def _has_index(bind, table: str, index: str) -> bool:
    inspector = sa.inspect(bind)
    return any(idx["name"] == index for idx in inspector.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if not _has_column(bind, "invitations", "accepted_by_user_id"):
        op.add_column(
            "invitations",
            sa.Column(
                "accepted_by_user_id",
                sa.dialects.postgresql.UUID(as_uuid=True)
                if is_postgres
                else sa.String(36),
                nullable=True,
            ),
        )
        # SQLite ALTER TABLE can't add inline FK; only emit on
        # Postgres where ``add_column`` is followed by an ALTER
        # ADD CONSTRAINT.
        if is_postgres:
            op.create_foreign_key(
                "fk_invitations_accepted_by_user_id_users",
                source_table="invitations",
                referent_table="users",
                local_cols=["accepted_by_user_id"],
                remote_cols=["id"],
                ondelete="SET NULL",
            )

    if not _has_index(bind, "invitations", "ix_invitations_token_hash"):
        op.create_index(
            "ix_invitations_token_hash",
            "invitations",
            ["token_hash"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if _has_index(bind, "invitations", "ix_invitations_token_hash"):
        op.drop_index("ix_invitations_token_hash", table_name="invitations")
    if is_postgres:
        try:
            op.drop_constraint(
                "fk_invitations_accepted_by_user_id_users",
                "invitations",
                type_="foreignkey",
            )
        except Exception:  # noqa: BLE001
            pass
        if _has_column(bind, "invitations", "accepted_by_user_id"):
            op.drop_column("invitations", "accepted_by_user_id")
    elif _has_column(bind, "invitations", "accepted_by_user_id"):
        # SQLite doesn't support a plain ``DROP COLUMN`` when an FK
        # references the column. Use batch_alter_table which copies
        # the table without the column. Same outcome, different
        # dialect quirk.
        with op.batch_alter_table("invitations") as batch_op:
            batch_op.drop_column("accepted_by_user_id")
