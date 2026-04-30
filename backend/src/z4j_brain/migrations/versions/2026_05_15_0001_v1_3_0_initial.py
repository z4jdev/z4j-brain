"""z4j 1.3.0 initial schema, full consolidated reset.

Revision ID: v1_3_0_initial
Revises: (none, first migration of 1.3.x line)
Create Date: 2026-05-15

This is the FIRST migration of the 1.3.x line. It contains the
ENTIRE z4j schema in one file: every table, every index, every
trigger, every constraint that 1.2.x ended up with after its 19
incremental migrations.

WHY ONE FILE? z4j 1.3.0 is a clean-slate reset. The 1.0/1.1/1.2
versions on PyPI were yanked (not deleted) when 1.3.0 shipped;
operators upgrading from any 1.x version are expected to
backup-then-fresh-restore. There is no in-place upgrade path
from 1.2.x, the 1.3.x migration history starts here.

WHAT'S IN HERE (sourced from 1.2.x migrations 0001-0020 minus
the deleted 0019/0021):
- 0001 initial schema (tables + Postgres extensions + audit
  triggers + events partitioning + Postgres-only indexes)
- 0002 invitation_acceptance (in model)
- 0003 password_reset_tokens (in model)
- 0004 scheduler_columns (in model)
- 0005 delivery_channel_snapshot (in model)
- 0006 pending_fires (in model)
- 0007 schedules_notify trigger (Postgres-only, lifted here)
- 0008 schedule_fires (in model)
- 0009 delivery_triggered_by (in model)
- 0010 pending_fires_expires_default (in model)
- 0011 scheduler_rate_buckets (in model)
- 0012 audit_chain_unique partial index (Postgres+SQLite, lifted)
- 0013 agent_workers (in model)
- 0014 projects.default_scheduler_owner (in model)
- 0015 audit_log_sweep_bypass (Postgres trigger function, the
  GUC-bypass version is installed here from the start)
- 0016 ix_audit_log_occurred_at (in model)
- 0017 audit_log.api_key_id (in model, NO foreign key by design;
  see audit_service.py for rationale)
- 0018 projects.allowed_schedulers (in model)
- 0020 ix_audit_log_api_key_id partial index (Postgres+SQLite,
  lifted here)

WHAT'S NOT INCLUDED:
- 0019 legacy_scheduler_migrate, was a one-shot 1.2.2 data
  migration that turned out to be a no-op. Operators who flip
  ``default_scheduler_owner`` after a project has stored
  schedules use the ``z4j-brain projects rewrite-scheduler``
  CLI for explicit migration.
- 0021 narrow_default_scheduler_owner, was a 1.2.2 column
  narrowing that we backed out. The Pydantic regex caps incoming
  values at 40 chars; the column staying at String(64) is
  harmless headroom.

COMPATIBILITY METADATA: this migration declares its compat
window via the ``compat`` dict. The brain reads it at startup
and refuses to apply migrations whose ``min_z4j_version`` is
above the running brain version. Future 1.3.x migrations carry
the same metadata, so an operator on z4j-brain 1.3.0 cannot
accidentally apply a migration that requires 1.5.0+.
"""

from __future__ import annotations

import socket
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.util import CommandError

from z4j_brain.persistence.base import Base


# ---------------------------------------------------------------------------
# Alembic revision identifiers
# ---------------------------------------------------------------------------

revision: str = "v1_3_0_initial"
down_revision: str | Sequence[str] | None = None  # FIRST 1.3.x migration
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Compatibility metadata (1.3.x convention)
# ---------------------------------------------------------------------------
#
# Read by ``z4j_brain.main.create_app`` at startup AND by the
# ``z4j-brain migrate check`` CLI. The brain refuses to start if
# its own version is below ``min_z4j_version`` for ANY migration
# in the chain, operators can never accidentally apply a
# migration their brain doesn't understand.
#
# - ``min_z4j_version``, earliest brain that can apply this migration
# - ``max_z4j_version``, latest brain that can apply this migration
#   (use ``"1.99.99"`` to mean "the entire 1.x line"; we will never
#   ship a 2.0 unless we genuinely re-architect)
# - ``upgrade_from``, the previous revision id (None = initial)
# - ``downgrade_to``, the revision this can downgrade to (None = initial)

compat = {
    "min_z4j_version": "1.3.0",
    "max_z4j_version": "1.99.99",
    "upgrade_from": None,
    "downgrade_to": None,
}


# ---------------------------------------------------------------------------
# Postgres-only DDL fragments (lifted from the 1.2.x migrations)
# ---------------------------------------------------------------------------

#: ``audit_log_forbid_mutation`` trigger function, GUC-bypass
#: variant from migration 0015. Installed from the start in 1.3.0
#: because the audit retention sweeper depends on it.
_AUDIT_FORBID_MUTATION_FN_SQL = """
CREATE OR REPLACE FUNCTION audit_log_forbid_mutation()
RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE'
     AND current_setting('z4j.audit_sweep', true) = 'on' THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;
"""

#: ``z4j_schedules_notify`` trigger function, from migration 0007.
#: Used by the brain's gRPC ``WatchSchedules`` handler to push
#: row-level INSERT/UPDATE/DELETE events to live schedulers.
_SCHEDULES_NOTIFY_FN_SQL = """
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


# ---------------------------------------------------------------------------
# upgrade() / downgrade()
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Create the full 1.3.0 schema."""
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        _install_extensions()

    # ORM model is the source of truth, it captures every table,
    # column, single + multi-column index declared via
    # ``__table_args__``, and FK constraint. SQLAlchemy emits the
    # right CREATE TABLE per dialect.
    Base.metadata.create_all(bind=bind)

    if is_postgres:
        _install_postgres_only_features(bind)
        _install_audit_log_triggers()
        _install_schedules_notify_trigger()
        _install_events_partitioning()
        _install_postgres_only_indexes()
    # The partial UNIQUE index on ``audit_log.prev_row_hmac`` and the
    # partial index on ``audit_log.api_key_id`` are declared in the
    # AuditLog model's ``__table_args__`` (with ``postgresql_where=``
    # / ``sqlite_where=`` predicates). ``Base.metadata.create_all``
    # handles them in both dialects, no separate ``op.execute``
    # needed here.


def downgrade() -> None:
    """Reverse the upgrade, drop everything."""
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        _drop_postgres_only_indexes()
        _drop_events_partitioning()
        _drop_schedules_notify_trigger()
        _drop_audit_log_triggers()
    # The partial indexes on audit_log are declared in __table_args__
    # so they're dropped automatically when audit_log is dropped below.

    # Drop tables in reverse-FK order (computed from
    # ``sa.schema.sort_tables_and_constraints``). CASCADE on
    # Postgres handles the FK web; SQLite needs the order to be
    # exactly correct.
    cascade = " CASCADE" if is_postgres else ""
    for table_name in (
        "schedule_fires",
        "alert_events",
        "task_annotations",
        "notification_deliveries",
        "user_notifications",
        "pending_fires",
        "events",
        "commands",
        "agent_workers",
        "workers",
        "tasks",
        "saved_views",
        "project_default_subscriptions",
        "user_subscriptions",
        "user_channels",
        "notification_channels",
        "sessions",
        "schedules",
        "queues",
        "password_reset_tokens",
        "memberships",
        "project_config",
        "user_preferences",
        "invitations",
        "export_jobs",
        "audit_log",
        "api_keys",
        "agents",
        "users",
        "z4j_meta",
        "scheduler_rate_buckets",
        "projects",
        "extension_store",
        "first_boot_tokens",
        "feature_flags",
    ):
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table_name}{cascade}"))

    if is_postgres:
        for enum_name in _SQL_ENUM_NAMES:
            op.execute(sa.text(f"DROP TYPE IF EXISTS {enum_name}"))


# ---------------------------------------------------------------------------
# Postgres helpers, dialect-guarded so SQLite tests skip them.
# ---------------------------------------------------------------------------


def _install_extensions() -> None:
    """``pgcrypto`` (gen_random_uuid), ``citext``, ``pg_trgm``."""
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS citext"))
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))


def _install_postgres_only_features(
    bind: sa.engine.Connection,  # noqa: ARG001
) -> None:
    """``projects.slug`` regex CHECK + UUIDv7 default on ``events.id``."""
    op.execute(
        sa.text(
            "ALTER TABLE projects ADD CONSTRAINT slug_format "
            "CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$')",
        ),
    )


def _install_audit_log_triggers() -> None:
    """REVOKE update/delete + raise-exception triggers on ``audit_log``.

    Uses the GUC-bypass variant from the start (1.2.x migration 0015):
    DELETE is permitted only when ``current_setting('z4j.audit_sweep')
    = 'on'``, the audit retention sweeper sets this per-batch.
    """
    op.execute(sa.text(_AUDIT_FORBID_MUTATION_FN_SQL))
    op.execute(
        sa.text(
            "CREATE TRIGGER audit_log_no_update "
            "BEFORE UPDATE ON audit_log "
            "FOR EACH ROW EXECUTE FUNCTION audit_log_forbid_mutation()",
        ),
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER audit_log_no_delete "
            "BEFORE DELETE ON audit_log "
            "FOR EACH ROW EXECUTE FUNCTION audit_log_forbid_mutation()",
        ),
    )
    # Defense-in-depth: trigger functions execute as their owner
    # by default; REVOKE EXECUTE FROM PUBLIC blocks direct invocation
    # via SQL but does NOT prevent CREATE OR REPLACE FUNCTION by a
    # role with CREATE on the schema. Operators wanting stricter
    # guarantees should use a least-privilege deploy role.
    op.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION audit_log_forbid_mutation() "
            "FROM PUBLIC",
        ),
    )


def _drop_audit_log_triggers() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS audit_log_no_delete ON audit_log"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS audit_log_forbid_mutation()"))


def _install_schedules_notify_trigger() -> None:
    """Install the schedules_changed pg_notify trigger."""
    op.execute(sa.text(_SCHEDULES_NOTIFY_FN_SQL))
    op.execute(
        sa.text(
            "CREATE TRIGGER z4j_schedules_notify_trigger "
            "AFTER INSERT OR UPDATE OR DELETE ON schedules "
            "FOR EACH ROW EXECUTE FUNCTION z4j_schedules_notify()",
        ),
    )


def _drop_schedules_notify_trigger() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS z4j_schedules_notify_trigger "
            "ON schedules",
        ),
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS z4j_schedules_notify()"))


# ---------------------------------------------------------------------------
# events partitioning (Postgres-only), lifted from migration 0001
# ---------------------------------------------------------------------------

#: Number of daily ``events`` partitions to pre-create at install
#: time. Operators rotate / extend via cron later; pre-creating
#: this many gives a fresh install enough runway to be useful
#: without ops attention.
_EVENTS_PRECREATE_DAYS: int = 7


def _install_events_partitioning() -> None:
    """Convert ``events`` to a partitioned table + pre-create N daily partitions.

    ``Base.metadata.create_all`` produced a regular table; Postgres
    requires CREATE TABLE ... PARTITION BY at create time, so we
    drop the empty regular table and recreate it as partitioned.
    Then pre-create N daily partitions starting today.
    """
    # Refuse to drop a non-empty events table, defensive guard
    # only matters in contrived scenarios but cheap to add.
    bind = op.get_bind()
    rows = list(bind.execute(sa.text("SELECT COUNT(*) FROM events")).fetchall())
    if rows and rows[0][0] > 0:
        raise CommandError(
            f"Refusing to drop 'events' table during partitioning "
            f"setup: it has {rows[0][0]} rows. This should never happen "
            f"on a fresh install. Investigate before re-running."
        )

    op.execute(sa.text("DROP TABLE events"))
    # Recreate as partitioned. Partition key MUST appear in the PK.
    op.execute(
        sa.text(
            "CREATE TABLE events ("
            "  id UUID NOT NULL DEFAULT gen_random_uuid(), "
            "  agent_id UUID NOT NULL, "
            "  task_id VARCHAR(80), "
            "  occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
            "  ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
            "  envelope JSONB NOT NULL DEFAULT '{}', "
            "  PRIMARY KEY (id, occurred_at)"
            ") PARTITION BY RANGE (occurred_at)",
        ),
    )
    # Indexes on the parent, Postgres propagates them to partitions.
    op.execute(
        sa.text(
            "CREATE INDEX ix_events_agent_occurred ON events "
            "(agent_id, occurred_at)",
        ),
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_events_task_occurred ON events "
            "(task_id, occurred_at) WHERE task_id IS NOT NULL",
        ),
    )

    # Pre-create N daily partitions starting today.
    op.execute(
        sa.text(
            "DO $$ "
            "DECLARE d DATE; "
            "BEGIN "
            f"FOR i IN 0..{_EVENTS_PRECREATE_DAYS - 1} LOOP "
            "  d := (CURRENT_DATE + i)::DATE; "
            "  EXECUTE format("
            "    'CREATE TABLE IF NOT EXISTS events_%s "
            "     PARTITION OF events "
            "     FOR VALUES FROM (%L) TO (%L)',"
            "    to_char(d, 'YYYY_MM_DD'), "
            "    d, "
            "    d + 1"
            "  ); "
            "END LOOP; "
            "END $$",
        ),
    )

    # DEFAULT partition catches anything outside the pre-created
    # window (clamp-bypass detection).
    op.execute(
        sa.text(
            "CREATE TABLE IF NOT EXISTS events_default "
            "PARTITION OF events DEFAULT",
        ),
    )


def _drop_events_partitioning() -> None:
    """Drop the partitioned events parent, partitions cascade."""
    op.execute(sa.text("DROP TABLE IF EXISTS events CASCADE"))


def _install_postgres_only_indexes() -> None:
    """GIN index on ``events.envelope`` for arbitrary JSON queries.

    The single index that's Postgres-specific (SQLite has no GIN).
    Lifted from migration 0001's ``_add_postgres_only_indexes``.
    """
    op.execute(
        sa.text(
            "CREATE INDEX ix_events_envelope_gin "
            "ON events USING GIN (envelope)",
        ),
    )


def _drop_postgres_only_indexes() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_events_envelope_gin"))


# ---------------------------------------------------------------------------
# Postgres ENUM names (for downgrade DROP TYPE)
# ---------------------------------------------------------------------------

# SQLAlchemy creates these as a side effect of the Enum columns;
# downgrade drops them after the tables are gone.
_SQL_ENUM_NAMES: tuple[str, ...] = (
    "schedulekind",
    "scheduleengine",
    "commandstatus",
    "agentstate",
    "projectrole",
    "userrole",
    "memberinvitestatus",
    "channelkind",
    "deliverystatus",
    "subscriptionscope",
)
