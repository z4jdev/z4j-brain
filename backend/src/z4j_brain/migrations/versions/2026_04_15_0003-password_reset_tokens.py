"""Add ``password_reset_tokens`` table.

Revision ID: 2026_04_15_0003_pw_reset
Revises: 2026_04_15_0002_invite_accept
Create Date: 2026-04-16

Single-use tokens for the password-reset flow. Shape mirrors
``first_boot_tokens`` plus ``user_id`` (so the confirm path
doesn't need to trust the email in the request body) and
``consumed_at`` (so the confirm path can mark tokens burned
rather than deleting them - keeps an audit trail).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2026_04_15_0003_pw_reset"
down_revision: str | Sequence[str] | None = "2026_04_15_0002_invite_accept"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if _has_table(bind, "password_reset_tokens"):
        # Defensive: the initial migration's ``Base.metadata.create_all``
        # already materializes this table on fresh installs. Only real
        # upgrade paths need the CREATE.
        return

    user_id_type = (
        sa.dialects.postgresql.UUID(as_uuid=True)
        if is_postgres
        else sa.String(36)
    )
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", user_id_type, primary_key=True),
        sa.Column("user_id", user_id_type, nullable=False),
        sa.Column("token_hash", sa.String, nullable=False),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=False,
        ),
        sa.Column(
            "consumed_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
            name="fk_password_reset_tokens_user_id_users",
        ),
    )
    op.create_index(
        "ix_password_reset_tokens_token_hash",
        "password_reset_tokens",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "password_reset_tokens"):
        return
    inspector = sa.inspect(bind)
    idx_names = {i["name"] for i in inspector.get_indexes("password_reset_tokens")}
    if "ix_password_reset_tokens_token_hash" in idx_names:
        op.drop_index(
            "ix_password_reset_tokens_token_hash",
            table_name="password_reset_tokens",
        )
    op.drop_table("password_reset_tokens")
