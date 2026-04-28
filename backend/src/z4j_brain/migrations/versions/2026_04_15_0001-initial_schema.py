"""initial schema

Revision ID: 2026_04_15_initial
Revises:
Create Date: 2026-04-15

Creates every brain table in one shot. Postgres-only features
(extensions, ENUM types, partitioning, append-only triggers, GIN
indexes) are wrapped in dialect checks so the same migration is
exercised against SQLite in unit tests without falling over.

There is no v0 to migrate forward from - splitting into 13 files
would just slow review without buying anything. The §6 list in
``docs/DATABASE.md`` is the table-of-contents for the body below.

Schema is reflected from ``Base.metadata.create_all()``, so every
column on the current SQLAlchemy models - ``users.first_name`` /
``last_name``, ``api_keys.scopes`` / ``project_id`` /
``revoked_reason``, every ``project_config`` column, etc. - gets
created here. There is no separate "add this column" revision
during pre-release: edit the model, bump the revision-ID date
below (``YYYY_MM_DD_initial``), drop+recreate the dev DB, ship.

PRE-RELEASE RESET POLICY
========================

Until v2026.4.0 lands on PyPI we explicitly reserve the right to
fold every schema change back into THIS revision. The revision ID
contains a date stamp so any DB created against an older snapshot
sits at an unknown revision and Alembic refuses to upgrade
silently - the operator sees::

    alembic.util.exc.CommandError: Can't locate revision identified
    by '2026_04_14_initial'

…which means "drop and recreate your dev DB". For the production
``z4jdev/z4j`` Docker image, the dev-mode SQLite path lives on a
named volume; ``docker volume rm z4j_data`` resets it.

Once v2026.4.0 lands on PyPI this policy ends. Future schema
changes land as additional revisions with proper ``op.add_column``
/ ``op.alter_column`` upgrade paths and Alembic does the
incremental work standard-style. The CHANGELOG marks the
transition explicitly.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

import sqlalchemy as sa
from alembic import op

from z4j_brain.persistence.base import Base
from z4j_brain.persistence import models  # noqa: F401  (registers metadata)
from z4j_brain.persistence.enums import SQL_ENUM_NAMES

#: Date-stamped revision ID. Bump the date when the schema in any
#: SQLAlchemy model changes; old dev DBs will refuse to start until
#: dropped + recreated. See "PRE-RELEASE RESET POLICY" above.
#:
#: 2026_04_15_v2 bump: added
#: ``ix_notification_deliveries_project_sent(project_id, sent_at DESC)``
#: to the NotificationDelivery model - backs the admin Delivery Log
#: page at scale. Picked up by ``Base.metadata.create_all`` below.
#: See docs/PRODUCTION_READINESS_2026Q2.md POL-1.
revision: str = "2026_04_15_v2_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Number of daily ``events`` partitions to pre-create.
#: After this the RetentionWorker takes over (B7).
_PRECREATE_PARTITION_DAYS: int = 7


def upgrade() -> None:
    """Create the full schema."""
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        _install_extensions()

    # SQLAlchemy understands every model declaration; let it emit
    # CREATE TABLE for the entire metadata in one pass. The Postgres
    # ENUM types are created by SQLAlchemy as a side-effect of the
    # ``Enum`` columns (we set ``create_type=True`` on each).
    Base.metadata.create_all(bind=bind)

    if is_postgres:
        _install_postgres_only_features(bind)
        _install_audit_log_triggers()
        _install_events_partitioning()
        _add_postgres_only_indexes()


def downgrade() -> None:
    """Reverse the upgrade.

    Drops in roughly reverse FK order. ENUM types are dropped after
    the tables that reference them.
    """
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        _drop_audit_log_triggers()
        _drop_events_partitioning()
        _drop_postgres_only_indexes()

    # Tables in reverse-FK order. CASCADE is Postgres-only;
    # SQLite drops in this exact order to satisfy FK constraints.
    cascade = " CASCADE" if is_postgres else ""
    for table_name in (
        "task_annotations",
        "alert_events",
        "extension_store",
        "project_config",
        "user_preferences",
        "feature_flags",
        "saved_views",
        "export_jobs",
        "invitations",
        "z4j_meta",
        "api_keys",
        "notification_deliveries",
        "user_notifications",
        "user_subscriptions",
        "project_default_subscriptions",
        "user_channels",
        "notification_channels",
        "first_boot_tokens",
        "audit_log",
        "sessions",
        "commands",
        "schedules",
        "events",
        "tasks",
        "workers",
        "queues",
        "agents",
        "memberships",
        "projects",
        "users",
    ):
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table_name}{cascade}"))

    if is_postgres:
        for enum_name in SQL_ENUM_NAMES:
            op.execute(sa.text(f"DROP TYPE IF EXISTS {enum_name}"))
        _drop_extensions()


# ---------------------------------------------------------------------------
# Postgres helpers - dialect-guarded so SQLite tests skip them.
# ---------------------------------------------------------------------------


def _install_extensions() -> None:
    """``pgcrypto``, ``citext``, ``pg_trgm``."""
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS citext"))
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))


def _drop_extensions() -> None:
    """Intentionally a no-op.

    Round-6 audit fix Mig-HIGH-3 (Apr 2026): the previous version of
    this function ran ``DROP EXTENSION IF EXISTS`` for ``pg_trgm``,
    ``citext`` and ``pgcrypto``. Postgres extensions are
    *database-scoped*, but in shared-database tenancy patterns (e.g.
    one Postgres database hosting both z4j-brain and another app's
    schema, separated by ``search_path``) these extensions are
    routinely depended on by the other tenant. A z4j downgrade would
    silently break the co-tenant's queries — an operator running
    ``z4j-brain migrate downgrade`` to roll back a bad release would
    not expect cluster-wide collateral damage.

    Extensions also cost effectively nothing to leave installed:
    they're metadata + (in pg_trgm/citext's case) a few KB of shared
    libraries. Leaving them in place is the safer default. Operators
    who genuinely want to remove them can ``DROP EXTENSION`` manually
    after confirming no other schema in the database references them.
    """
    return


def _install_postgres_only_features(bind: sa.engine.Connection) -> None:
    """``projects.slug`` regex CHECK + UUIDv7 default on ``events.id``."""
    op.execute(
        sa.text(
            "ALTER TABLE projects ADD CONSTRAINT slug_format "
            "CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$')",
        ),
    )

    server_version = bind.dialect.server_version_info or (0,)
    if server_version >= (18,):
        # Postgres 18+ exposes uuidv7() in core. Time-ordered ids on
        # the partitioning hot table give us much better B-tree
        # locality than random uuid4 values.
        op.execute(
            sa.text("ALTER TABLE events ALTER COLUMN id SET DEFAULT uuidv7()"),
        )
    else:
        op.execute(
            sa.text(
                "ALTER TABLE events ALTER COLUMN id SET DEFAULT gen_random_uuid()",
            ),
        )

    # Server-side defaults for the other UUID PKs so callers that
    # bypass SQLAlchemy still get a UUID without supplying one.
    for table in (
        "users", "projects", "memberships", "agents", "queues", "workers",
        "tasks", "schedules", "commands", "audit_log", "first_boot_tokens",
        "sessions",
        "notification_channels", "user_channels", "user_subscriptions",
        "project_default_subscriptions", "user_notifications",
        "notification_deliveries",
    ):
        op.execute(
            sa.text(
                f"ALTER TABLE {table} "
                f"ALTER COLUMN id SET DEFAULT gen_random_uuid()",
            ),
        )

    # Promote text-typed IP columns to native INET on Postgres so
    # the production schema matches the model declarations exactly.
    op.execute(
        sa.text(
            "ALTER TABLE users "
            "ALTER COLUMN last_failed_login_ip TYPE INET "
            "USING last_failed_login_ip::INET",
        ),
    )
    op.execute(
        sa.text(
            "ALTER TABLE sessions "
            "ALTER COLUMN ip_at_issue TYPE INET "
            "USING ip_at_issue::INET",
        ),
    )


def _install_audit_log_triggers() -> None:
    """REVOKE update/delete + raise-exception triggers on ``audit_log``."""
    op.execute(
        sa.text(
            "CREATE OR REPLACE FUNCTION audit_log_forbid_mutation() "
            "RETURNS trigger AS $$ "
            "BEGIN "
            "  RAISE EXCEPTION 'audit_log is append-only'; "
            "END; "
            "$$ LANGUAGE plpgsql",
        ),
    )
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
    op.execute(sa.text("REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC"))


def _drop_audit_log_triggers() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS audit_log_no_delete ON audit_log"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS audit_log_forbid_mutation()"))


def _install_events_partitioning() -> None:
    """Convert the freshly-created ``events`` table into a partitioned one.

    SQLAlchemy ``create_all`` cannot emit ``PARTITION BY`` directly,
    so we drop the empty table and recreate it with the partitioning
    clause. This is safe at migration time because the table is
    guaranteed empty (we just created it).

    Then pre-create N daily partitions starting today.
    """
    # Round-6 audit fix Mig-HIGH-1 (Apr 2026): defensively guard the
    # DROP. If ``create_all`` skipped ``events`` (e.g. user pre-applied
    # an out-of-band schema patch) the unconditional DROP failed mid-
    # migration. If the table somehow already holds rows, bail loudly
    # rather than silently destroying audit data.
    bind = op.get_bind()
    exists_row = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = 'events'"
        ),
    ).first()
    if exists_row is not None:
        rows = bind.execute(sa.text("SELECT count(*) FROM events")).scalar()
        if rows and rows > 0:
            raise RuntimeError(
                f"Refusing to drop 'events' table during partitioning "
                f"install: table holds {rows} rows. This migration is "
                f"only safe on an empty schema. Restore from backup or "
                f"manually migrate rows before continuing.",
            )
        op.execute(sa.text("DROP TABLE IF EXISTS events"))
    op.execute(
        sa.text(
            "CREATE TABLE events ("
            "  id           UUID NOT NULL DEFAULT gen_random_uuid(), "
            # RESTRICT (not CASCADE) on project_id / agent_id -
            # see event model docstring. A 50M-row project's
            # CASCADE would lock every daily partition in one
            # transaction. RESTRICT forces operators to purge
            # events via retention FIRST, then delete the project.
            "  project_id   UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT, "
            "  agent_id     UUID NOT NULL REFERENCES agents(id) ON DELETE RESTRICT, "
            "  engine       TEXT NOT NULL, "
            "  task_id      TEXT NOT NULL DEFAULT '', "
            "  kind         TEXT NOT NULL, "
            "  occurred_at  TIMESTAMPTZ NOT NULL, "
            "  payload      JSONB NOT NULL DEFAULT '{}'::jsonb, "
            # PK includes ``project_id`` so a Project-A agent
            # cannot collide its event_id with a Project-B row
            # via ON CONFLICT DO NOTHING (R3 finding H1).
            # ``occurred_at`` must remain in the PK because
            # Postgres requires the partition column there.
            "  PRIMARY KEY (project_id, occurred_at, id) "
            ") PARTITION BY RANGE (occurred_at)",
        ),
    )

    # Restore the uuidv7 / gen_random_uuid default if PG18+.
    bind = op.get_bind()
    server_version = bind.dialect.server_version_info or (0,)
    if server_version >= (18,):
        op.execute(
            sa.text("ALTER TABLE events ALTER COLUMN id SET DEFAULT uuidv7()"),
        )

    # Indexes on the parent - Postgres propagates them to partitions.
    op.execute(
        sa.text(
            "CREATE INDEX ix_events_project_task "
            "ON events (project_id, task_id, occurred_at DESC)",
        ),
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_events_project_kind "
            "ON events (project_id, kind, occurred_at DESC)",
        ),
    )

    # DEFAULT partition. Catches anything outside the
    # pre-created daily windows so a malicious or buggy agent
    # supplying a far-future / far-past ``occurred_at`` cannot
    # blow up ingest with ``no partition of relation "events"
    # found`` (R3 finding C2). The brain ALSO clamps incoming
    # timestamps in ``EventIngestor._clamp_occurred_at`` so this
    # table normally stays empty; alerting on its row count
    # surfaces clamp bypasses or partition-creator gaps.
    op.execute(
        sa.text(
            "CREATE TABLE IF NOT EXISTS events_default "
            "PARTITION OF events DEFAULT",
        ),
    )

    # Pre-create N daily partitions. Naming convention matches the
    # RetentionWorker (B7) so it can find and drop them.
    today = date.today()
    for offset in range(_PRECREATE_PARTITION_DAYS):
        day = today + timedelta(days=offset)
        next_day = day + timedelta(days=1)
        partition_name = f"events_{day:%Y_%m_%d}"
        op.execute(
            sa.text(
                f"CREATE TABLE IF NOT EXISTS {partition_name} "
                f"PARTITION OF events "
                f"FOR VALUES FROM ('{day.isoformat()}') "
                f"TO ('{next_day.isoformat()}')",
            ),
        )


def _drop_events_partitioning() -> None:
    """Drop the partitioned events table and all its child partitions.

    Postgres ``DROP TABLE events CASCADE`` removes child partitions
    automatically.
    """
    op.execute(sa.text("DROP TABLE IF EXISTS events CASCADE"))


def _add_postgres_only_indexes() -> None:
    """GIN, partial, and full-text indexes that SQLite cannot represent."""
    # Tasks: GIN on JSONB args/kwargs (containment search), GIN on
    # the search_vector column, partial idx on parent/root.
    op.execute(
        sa.text(
            "CREATE INDEX ix_tasks_args_gin ON tasks "
            "USING GIN (args jsonb_path_ops)",
        ),
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_tasks_kwargs_gin ON tasks "
            "USING GIN (kwargs jsonb_path_ops)",
        ),
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_tasks_search ON tasks "
            "USING GIN (search_vector)",
        ),
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_tasks_parent ON tasks (project_id, parent_task_id) "
            "WHERE parent_task_id IS NOT NULL",
        ),
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_tasks_root ON tasks (project_id, root_task_id) "
            "WHERE root_task_id IS NOT NULL",
        ),
    )

    # Tasks: composite index for priority + state filtering.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_tasks_project_priority_state "
            "ON tasks (project_id, priority, state)",
        ),
    )

    # Commands: index for the long-poll command-pull query
    # ``WHERE agent_id=? AND status='pending' ORDER BY issued_at``.
    # Without it a long-poll storm walks the whole commands table
    # four times per second per agent (R4 follow-up). Partial on
    # status='pending' keeps the index tiny - only undelivered
    # commands ever live in it.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_commands_agent_pending "
            "ON commands (agent_id, issued_at) "
            "WHERE status = 'pending'",
        ),
    )

    # Tasks: pg_trgm GIN indexes on the columns the dashboard
    # task list searches via leading-wildcard ILIKE
    # (``%needle%``). Without these, the ``search_query`` filter
    # is a sequential scan over the entire tasks partition - fine
    # at 10k rows, lethal at 50M. The ``search_vector`` GIN
    # covers tsquery / tsvector lookups (a future upgrade), but
    # NOT the substring ILIKE the UI actually issues today.
    op.execute(
        sa.text(
            "CREATE INDEX ix_tasks_name_trgm ON tasks "
            "USING GIN (name gin_trgm_ops)",
        ),
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_tasks_queue_trgm ON tasks "
            "USING GIN (queue gin_trgm_ops) "
            "WHERE queue IS NOT NULL",
        ),
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_tasks_worker_trgm ON tasks "
            "USING GIN (worker_name gin_trgm_ops) "
            "WHERE worker_name IS NOT NULL",
        ),
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_tasks_taskid_trgm ON tasks "
            "USING GIN (task_id gin_trgm_ops)",
        ),
    )

    # Schedules: partial idx for the scheduler tick.
    op.execute(
        sa.text(
            "CREATE INDEX ix_schedules_next_run ON schedules (next_run_at) "
            "WHERE is_enabled",
        ),
    )

    # Commands: partial idx for the timeout sweep.
    op.execute(
        sa.text(
            "CREATE INDEX ix_commands_pending_timeout "
            "ON commands (timeout_at) "
            "WHERE status IN ('pending', 'dispatched')",
        ),
    )

    # Audit log: prefix-pattern index used by
    # ``count_recent_by_action_and_ip`` (setup + login brute-force
    # rate limiting). Without this, the LIKE 'prefix%' query falls
    # back to a heap scan on every setup / login attempt - the
    # exact path an attacker hammers (audit H17). The
    # ``text_pattern_ops`` operator class is what makes LIKE
    # 'foo%' index-eligible on a btree index.
    op.execute(
        sa.text(
            "CREATE INDEX ix_audit_log_action_pattern "
            "ON audit_log (action text_pattern_ops, occurred_at DESC)",
        ),
    )

    # Events: project + occurred_at composite for /home/summary
    # aggregates that don't predicate on kind. Audit perf F13:
    # the existing kind-leading indexes can't skip-scan across
    # kinds for unfiltered counts.
    op.execute(
        sa.text(
            "CREATE INDEX ix_events_project_occurred "
            "ON events (project_id, occurred_at DESC)",
        ),
    )

    # Users: partial idx on active rows.
    op.execute(
        sa.text(
            "CREATE INDEX ix_users_active_partial ON users (is_active) "
            "WHERE is_active",
        ),
    )

    # Projects: partial idx on active rows.
    op.execute(
        sa.text(
            "CREATE INDEX ix_projects_active_partial ON projects (is_active) "
            "WHERE is_active",
        ),
    )

    # Sessions: partial idx on live sessions for the per-user
    # "list active sessions" lookup the dashboard renders.
    op.execute(
        sa.text(
            "CREATE INDEX ix_sessions_user_active "
            "ON sessions (user_id) WHERE revoked_at IS NULL",
        ),
    )


    # Notification channels: project lookup.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_notification_channels_project "
            "ON notification_channels (project_id)",
        ),
    )

    # User channels: per-user lookup (one query per settings page load).
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_user_channels_user "
            "ON user_channels (user_id)",
        ),
    )

    # User subscriptions: dispatcher lookup by (project, trigger).
    # Partial index on active rows because dispatch only cares about
    # is_active = TRUE - inactive subs don't fire.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_user_subs_project_trigger "
            "ON user_subscriptions (project_id, trigger) "
            "WHERE is_active",
        ),
    )

    # User subscriptions: settings page lookup ("show me my subs").
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_user_subs_user "
            "ON user_subscriptions (user_id)",
        ),
    )

    # Project default subscriptions: lookup at member-join time.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_project_default_subs_project "
            "ON project_default_subscriptions (project_id)",
        ),
    )

    # User notifications: bell unread query.
    # Partial index on unread rows because the bell badge query is
    # ``WHERE user_id = $1 AND read_at IS NULL`` - the partial index
    # keeps the read history out of the hot path.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_user_notifications_unread "
            "ON user_notifications (user_id, created_at DESC) "
            "WHERE read_at IS NULL",
        ),
    )

    # User notifications: full bell list (read + unread).
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_user_notifications_user "
            "ON user_notifications (user_id, created_at DESC)",
        ),
    )

    # Notification deliveries: project audit log.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_notification_deliveries_project "
            "ON notification_deliveries (project_id, sent_at DESC)",
        ),
    )


def _drop_postgres_only_indexes() -> None:
    """Inverse of :func:`_add_postgres_only_indexes`."""
    for index_name in (
        "ix_notification_deliveries_project",
        "ix_user_notifications_user",
        "ix_user_notifications_unread",
        "ix_project_default_subs_project",
        "ix_user_subs_user",
        "ix_user_subs_project_trigger",
        "ix_user_channels_user",
        "ix_notification_channels_project",
        "ix_sessions_user_active",
        "ix_projects_active_partial",
        "ix_users_active_partial",
        "ix_commands_pending_timeout",
        "ix_schedules_next_run",
        "ix_commands_agent_pending",
        "ix_tasks_taskid_trgm",
        "ix_tasks_worker_trgm",
        "ix_tasks_queue_trgm",
        "ix_tasks_name_trgm",
        "ix_tasks_project_priority_state",
        "ix_tasks_root",
        "ix_tasks_parent",
        "ix_tasks_search",
        "ix_tasks_kwargs_gin",
        "ix_tasks_args_gin",
    ):
        op.execute(sa.text(f"DROP INDEX IF EXISTS {index_name}"))
