# Changelog

All notable changes to this package are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.4] - 2026-04-30

**Per-agent version visibility on the dashboard, with no telemetry.**

The Agents page now shows each agent's z4j-core version next to
its name, with a small badge when an *update is available* or
when the agent is *incompatible* (major version mismatch). All
comparisons run against a versions snapshot bundled into the
brain wheel itself - no automatic outbound HTTP, no telemetry.
An admin-only *Check for updates* button on Settings -> System
fetches a fresher snapshot from GitHub on demand (configurable,
disable-able).

### Added

- **Bundled versions snapshot**: every brain wheel now ships
  `z4j_brain/data/versions.json` listing the latest known version
  of every z4j package as of this brain's release. Generated from
  `sites/_shared/packages.ts` (the canonical source the websites
  also consume) by `scripts/gen-versions-json.py`. Read at brain
  startup into `app.state.versions_snapshot`.
- **`agent_version` persistence**: `AgentRepository.mark_online`
  stores the agent's hello-frame version under
  `agent_metadata['version']` on every connect. No DB migration
  (existing JSON column).
- **Agents API** (`GET /api/v1/projects/{slug}/agents`) now
  returns `agent_version: str | null` and a computed
  `version_status: "current" | "outdated" | "newer_than_known" |
  "incompatible" | "unknown" | null` per agent.
- **Admin endpoints**:
  - `GET /api/v1/admin/system/versions` - returns the cached
    snapshot (bundled or remote-refreshed) + the configured
    check-for-updates URL.
  - `POST /api/v1/admin/system/versions/check` - operator-
    initiated remote refresh. Fetches `Z4J_VERSION_CHECK_URL`
    (default GitHub raw URL of the umbrella repo's
    `versions.json`), validates the response, atomic-swaps the
    in-memory snapshot. Strict failure modes (HTTPS-only,
    256 KB cap, schema validation) keep a hostile mirror from
    poisoning the brain.
- **Setting `version_check_url`** (env: `Z4J_VERSION_CHECK_URL`).
  Default: `https://raw.githubusercontent.com/z4jdev/z4j/main/versions.json`.
  Set to empty to hide the *Check for updates* button entirely
  (paranoid / strict no-outbound deploys keep the bundled
  snapshot only). Set to a private mirror URL for air-gapped
  fleets.
- **Dashboard**:
  - VERSION column on the Agents page with the per-status badge.
    Tooltip explains the recommended action (`pip install -U`,
    *upgrade required*, etc.).
  - Settings -> System: *Update checks* card showing the
    snapshot's age, source (`bundled` / `remote`), the configured
    check URL, and a *Check for updates* button. Toast on
    success; clean error on any failure mode (URL empty,
    non-200, oversized, schema invalid).
- **`scripts/gen-versions-json.py`**: parses
  `sites/_shared/packages.ts` into the bundled `versions.json`.
  Hooked into `release-split.sh` so every release wave
  regenerates automatically.

### Privacy posture

Reaffirmed and tightened: **no automatic background polling, no
telemetry**. The bundled snapshot is the default source of truth.
The remote URL is fetched ONLY when an admin explicitly clicks
*Check for updates*. The default URL is GitHub raw (universally
allowlisted, tamper-evident in git history, served via GitHub's
CDN). Operators who want zero outbound HTTP set the env var
empty and the button disappears.

### Tests

- 36 new tests in `test_version_check.py` covering the SemVer
  parser, snapshot validator, every `compare()` status branch,
  bundled-file loader, and every documented `fetch_remote()`
  failure mode (empty URL, non-https, non-200, invalid JSON,
  oversized response, invalid schema). Full brain unit suite:
  738 passed.

### Compatibility

Drop-in upgrade from 1.3.3. Schema unchanged (uses the existing
`agent_metadata` JSON column). No migration. Floor unchanged
(`z4j-core>=1.3.1`). Older agents that don't report a version
render `-` in the VERSION column - no errors.

## [1.3.3] - 2026-04-30

**Schedule snapshot reconciliation: brain ingests full-inventory
events from agents and surfaces a *Sync now* button on the
Schedules page.**

Closes the onboarding gap where existing celery-beat /
rq-scheduler / apscheduler / arqcron / hueyperiodic /
taskiqscheduler schedules were invisible to the dashboard until
the operator edited each one. Companion agent-side feature in
z4j-bare 1.3.1.

### Added

- `EventIngestor` handles `EventKind.SCHEDULE_SNAPSHOT`. The event
  payload carries `{scheduler, schedules, reason}`; the ingestor
  delegates to `ScheduleRepository.reconcile_snapshot` for the
  3-way diff against the DB.
- `ScheduleRepository.reconcile_snapshot(project_id, scheduler,
  schedules) -> {inserted, updated, deleted}`. Inserts new rows,
  updates existing rows, deletes rows present in the brain for
  this `(project, scheduler)` but missing from the snapshot.
  Per-scheduler scope: a celery-beat snapshot never prunes
  apscheduler rows in the same project.
- `POST /api/v1/projects/{slug}/schedules:resync` — admin-only,
  requires CSRF and bulk-action throttle. Dispatches a
  `schedule.resync` command to every online agent in the project
  that advertises at least one scheduler adapter; agents respond
  by draining their adapters and emitting fresh
  `SCHEDULE_SNAPSHOT` events. Returns 202 with a count of
  agents dispatched + the distinct scheduler adapter names
  observed; the actual reconciliation arrives async via the
  event pipeline.
- Dashboard *Sync now* button on `/projects/{slug}/schedules`
  (next to *Refresh* and *Reconcile diff*). Visible to admins
  only. Calls the new endpoint, then auto-refetches the schedule
  list at 0 s and 3 s so the user sees the reconciled view
  without a manual refresh. The hook is exposed as
  `useScheduleResync(slug)` for any other place that wants to
  trigger a sync.

### Tests

- 7 new tests under `test_schedule_reconcile_snapshot.py` covering
  insert / update / delete-missing / empty snapshot deletes-all /
  per-scheduler scoping (celery-beat doesn't prune apscheduler) /
  per-project scoping (one project's snapshot doesn't touch
  another's rows). Full brain unit suite: 702 passed.

### Compatibility

Drop-in upgrade from 1.3.2. No DB migration. Floor bumped to
`z4j-core>=1.3.1` for the new `EventKind` value. Agents on
z4j-bare 1.3.0 keep working — they don't emit the snapshot kind,
the brain just doesn't surface anything new for them. Upgrade
agents to z4j-bare 1.3.1 to light up the feature end-to-end.

## [1.3.2] - 2026-04-30

**Hotfix: global admin can't create per-user subscriptions.**

A global brain admin (`user.is_admin=True`, the kind created by
`z4j bootstrap-admin`) was being rejected by
`POST /api/v1/user/subscriptions` with **403 *you are not a member
of this project***, even though `/api/v1/auth/me` synthesises an
admin-grade membership for them on every project (so the dashboard
project switcher and the `/settings/memberships` page list them
with full admin badges). Direct contradiction between the two
endpoints. The "New Subscription" modal in the dashboard was
unusable for global admins on any project they hadn't been
explicitly added to with a `Membership` row.

### Fixed

- `api/user_notifications.py::create_user_subscription` now uses
  the canonical `PolicyEngine.require_member` helper (with
  `min_role=ProjectRole.VIEWER`), which already handles the
  `is_admin` bypass uniformly. The sibling list endpoint already
  short-circuited on `not user.is_admin`; the create endpoint
  had drifted out of sync.

### Tests

- `test_subscription_create_global_admin.py` — two new
  regression tests:
  1. A `is_admin=True` user with NO `Membership` row on a project
     can `POST /user/subscriptions` against it (returns 201, was
     403 pre-1.3.2).
  2. A regular `is_admin=False` non-member is STILL blocked (the
     bypass is gated on `is_admin`, not opened up to everyone).

### Audit scope

- Verified the bug was confined to `create_user_subscription`.
  Every other endpoint that consults the `Membership` table on
  behalf of the *caller* (not a target user being invited / added)
  goes through `PolicyEngine.require_member`, which has handled
  `is_admin` since v1.0.x. `list_user_subscriptions`,
  `import_user_channel_from_project`, `audit.py`, etc., are all
  correct.
- `update_user_subscription`, `delete_user_subscription`,
  `list_user_deliveries`, channel CRUD endpoints are scoped by
  `user.id` (owner-only by design) and don't perform project
  membership checks at all — also correct.

### Compatibility

Drop-in upgrade from 1.3.1. Schema unchanged. Floors unchanged.
No migration. Restart the brain after `pip install --upgrade
z4j-brain` and global admins can create per-user subscriptions
on any project they see in the switcher.

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
