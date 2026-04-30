# Changelog

All notable changes to this package are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.1] - 2026-04-30

**Hotfix for the heartbeat → worker upsert path.**

Every Postgres + SQLite deployment that upgraded to 1.3.0 saw an
empty Workers tab on the dashboard, even though the agent reported
online. Two distinct bugs in the persistence layer raised
``AttributeError`` / ``UndefinedColumn`` for any code path that
touched ``Worker.worker_metadata`` or ``Agent.agent_metadata``
through anything other than the ORM. Both are model-attribute
versus DB-column name mismatches: ``Worker.worker_metadata``
stores in DB column ``metadata`` (the prefix is only there to
avoid clashing with SQLAlchemy's reserved ``Base.metadata``).

### Fixed

- `repositories/workers.py` — `upsert_from_events_bulk` translates
  `worker_metadata` → `metadata` for both the dialect-level
  `insert().values()` payload and the `stmt.excluded.<col>`
  lookup. Pre-1.3.1 the bulk path raised
  `AttributeError: worker_metadata` on every heartbeat that
  carried a metadata payload (which is every heartbeat in
  practice — the agent always populates it). The exception was
  swallowed by the outer `except Exception` in
  `frame_router._handle_heartbeat`, so the user saw an
  *agent-online, no-workers* state rather than a hard error.
- `repositories/agents.py` — the Postgres-only `jsonb_set` raw
  SQL referenced `agent_metadata` as a column name, which doesn't
  exist as a real column. Pre-1.3.1 every Postgres agent connect
  that supplied a `host` payload raised `UndefinedColumn`. Fixed
  to reference the underlying DB column name `metadata`. SQLite
  was not affected (it goes through the ORM RMW fallback).

### Tests

- `test_workers_repo_bulk_upsert.py::TestBulkUpsertWorkerMetadata`
  exercises the bulk path with a real `worker_metadata` payload
  on insert AND on conflict-do-update — two regression tests
  that fail against pre-1.3.1 code.
- `test_frame_router_heartbeat_e2e.py` is a new file that boots a
  real `FrameRouter` against an in-memory SQLite DB and dispatches
  a `HeartbeatFrame` shaped exactly the way `z4j-celery`'s
  `CeleryEngine.health()` produces: `adapter_health` carrying
  `celery.worker_details` as a JSON-serialised dict of
  `{hostname: {stats, active, active_queues, registered, conf}}`.
  This is the path that runs in production and the path that
  the unit-level repo tests didn't exercise — closing that
  coverage gap so a similar bug can't ship again.

### Compatibility

Drop-in upgrade from 1.3.0. Schema unchanged. Floors unchanged
(`z4j-core>=1.3.0,<2`). No migration needed.

## [1.3.0] - 2026-05-15

**Initial release of the 1.3.x line.**

z4j 1.3.0 is a clean-slate reset of the 1.x ecosystem. All prior
1.x versions on PyPI (1.0.x, 1.1.x, 1.2.x) are yanked — they
remain installable by exact pin but `pip install` no longer
selects them. Operators upgrading from any prior 1.x deployment
are expected to back up their database and run a fresh install
against 1.3.x; there is no in-place migration path.

### Why the reset

The 1.0/1.1/1.2 line accumulated complexity organically across
many small releases. By 1.2.2 the codebase carried defensive
shims, deep audit-history annotations, and a 19-step alembic
migration chain that made onboarding harder than it needed to
be. 1.3.0 ships the same feature set as 1.2.2 but with:

- One consolidated alembic migration containing the entire
  schema, with explicit `compat` metadata declaring the version
  window in which it can be applied.
- HMAC canonical form starts at v1 (no v1→v4 fallback chain in
  the verifier).
- Defensive `getattr` shims removed for fields that exist in the
  final model.
- "Audit fix Round-N" annotations removed from the codebase.

### Release discipline (new)

PyPI publishes now require an explicit `Z4J_PUBLISH_AUTHORIZED=1`
environment variable to be set in the publish-script invocation.
The 1.0-1.2 wave shipped patches too quickly and had to yank/
unyank versions; the new gate makes that mistake impossible.

### Migrating from 1.x

1. Back up your database (`z4j-brain backup --out backup.sql`).
2. Bring the brain down.
3. `pip install -U z4j` to pick up 1.3.0.
4. `z4j-brain migrate upgrade head` runs the consolidated
   migration; it detects an empty `alembic_version` table and
   applies the single `v1_3_0_initial` revision.
5. Bring the brain back up. The dashboard, audit log, and
   schedule data structures are preserved across the migration
   when the operator restores from the backup; if you started
   fresh, you'll see an empty brain.

### See also

- `CHANGELOG-1.x-legacy.md` in this package's source tree for
  the complete 1.0/1.1/1.2 release history.

## [Unreleased]
