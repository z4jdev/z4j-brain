# Changelog

All notable changes to `z4j-brain` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.19] - 2026-04-27

> **Theme: the last fully-stable v1.0.x patch.** Pre-1.0.19, downgrading
> from a newer brain to an older one could flap-loop alembic, and
> 1.0.18 silently introduced 3 always-on workers that consumed
> connection-pool slots. From 1.0.19 onward every v1.0.x version
> upgrades and downgrades cleanly to / from every other v1.0.x. See
> `docs/MIGRATIONS.md` for the additive-only contract.
>
> Coordinated minor publish: ships alongside `z4j-core` 1.0.5 (the
> SDK companion that adds `Schedule.catch_up` / `source` /
> `source_hash` defaults so deserializing brain responses doesn't
> trip `extra="forbid"`) and the `z4j` umbrella 1.0.19. The bigger
> v1.1.0 baseline (embedded scheduler sidecar, `z4j-scheduler` 1.1.0,
> reconciliation `:diff` UI, ecosystem-wide adapter rebumps) ships
> after 1.0.19 has soaked.

### Fixed

- **C1 — schema-version skew now warns instead of raising.**
  `startup_version.check_and_update_schema_version` previously raised
  `SchemaVersionError` if the DB's `z4j_meta.schema_version` was newer
  than the running code, killing boot. After downgrade (1.0.18 → 1.0.17)
  this caused systemd flap loops. Now logs a warning and continues —
  old code can boot against forward-migrated DBs. The
  `SchemaVersionError` class is kept as a back-compat shim for any
  downstream import. Pinned by
  `tests/unit/test_compat_fixes.py::TestC1SchemaVersionWarnNotRaise`.
- **C2 — `auto-migrate` detects unknown DB head before invoking
  alembic.** When the DB is stamped at a revision the running wheel
  doesn't ship (downgrade scenario), alembic exited with `Can't locate
  revision identified by '…'` and the brain flap-looped. Now
  `_detect_unknown_db_head` pre-flights the DB's `alembic_version`
  against the code's known revisions and raises a clean
  `_UnknownDBRevisionError` that the serve handler catches → warns +
  continues boot. Pinned by
  `tests/unit/test_compat_fixes.py::TestC2AutoMigrateUnknownRevision`.
- **H1 — scheduler workers gated behind `Z4J_SCHEDULER_GRPC_ENABLED`.**
  v1.0.18 shipped 3 unconditional periodic workers
  (`pending_fires_replay`, `schedule_circuit_breaker`,
  `schedule_fires_prune`) that consumed connection-pool slots.
  Operators reported agents flapping to "offline" after upgrade
  because the heartbeat path couldn't get a slot. These three workers
  now only register when the gRPC scheduler service is opted in.
  Default-off operators get zero scheduler-worker activity. Pinned by
  `tests/unit/test_compat_fixes.py::TestH1SchedulerWorkersGated`.
- **H2 — `SubscriptionFilters` relaxed to `extra="ignore"`.** Pre-1.0.19
  this Pydantic model used `extra="forbid"`, so a newer dashboard
  bundle adding any unknown filter key would 422 against an older
  brain. Now unknown keys are silently dropped. The two
  security-relevant `extra="forbid"` schemas (`BulkDeleteRequest`,
  `UserSubscriptionCreate` — both R3 audit defenses against
  `project_id` / `user_id` smuggling) are deliberately NOT relaxed.
  Pinned by
  `tests/unit/test_compat_fixes.py::TestH2SubscriptionFiltersExtraIgnore`.
- **M2 — SPA fallback `index.html` ships no-cache headers.**
  Pre-1.0.19 the dashboard SPA entry point was served with default
  caching, so browsers held a stale `index.html` (referencing
  hashed asset filenames that no longer existed) after a brain
  upgrade. The fallback now sends `Cache-Control: no-cache,
  no-store, must-revalidate` — hashed assets under `/assets/` keep
  their long-lived caching, by design. Pinned by
  `tests/unit/test_compat_fixes.py::TestM2DashboardCacheControl`.
- **gRPC `too_many_pings` reconnect storm** (only affects operators
  with `Z4J_SCHEDULER_GRPC_ENABLED=1`). The brain's
  `SchedulerGrpcServer` was using stock defaults for
  `keepalive_min_recv_ping_interval_without_data_ms` (300_000 ms),
  so the scheduler client's 30 s keepalive was treated as abuse and
  the server sent `GOAWAY too_many_pings` every ~150 seconds. The
  watch stream then reconnected and re-issued a full sync. Net
  effect: 12 – 20 spurious watch reconnects per hour silently
  churning the cache. Now sets matching server options
  (`min_recv_ping_interval_without_data_ms=10_000`,
  `http2.min_ping_interval_without_data_ms=10_000`,
  `http2.max_ping_strikes=0`).
- **mTLS interceptor accepted bytes-keyed AuthContext only** (only
  affects operators with `Z4J_SCHEDULER_GRPC_ENABLED=1`).
  `_enforce_cn` looked up the peer cert under
  `auth_ctx.get(b"x509_common_name", [])`. grpc.aio 1.6+ returns
  the same logical entries under str keys, so every CN check
  silently returned `[]` and any client cert was rejected as
  `peer CNs []`. Now looks up under both shapes and decodes
  bytes / str values defensively. Pinned by
  `tests/unit/test_scheduler_grpc.py::TestEnforceCnAuthContextShape`.

### Added

- **`z4j-brain migrate sync`** — recovery escape hatch for operators
  who land in DB-ahead-of-code state in spite of the new contract.
  Default behavior shows the drift and refuses to act. With
  `--allow-future-schema --i-know-this-can-corrupt-data` it stamps
  the DB to the code's head and drops unknown tables. The
  double-confirm flag is intentional — this is a recovery tool,
  not a routine operation. Documented in `docs/MIGRATIONS.md`.

### Documentation

- **`docs/MIGRATIONS.md`** — the binding additive-only contract for
  the v1.0.x line. Five rules (`server_default` for NOT NULL, no
  destructive ops within a minor, new tables tolerate empty, new
  workers gated behind a setting, `extra="forbid"` reserved for
  privilege-smuggling defenses) plus the `migrate sync` recovery
  escape hatch.

## [1.0.18] - 2026-04-27

### Added

- **Edit personal subscriptions in the dashboard.** Pre-1.0.18 the
  `/settings/notifications` page only let users toggle `is_active`
  + delete. Adjusting channels, filters, cooldown, or trigger
  required delete-and-recreate. The `PATCH /api/v1/user/subscriptions/{id}`
  endpoint had supported every field except `trigger` since v1.0.x;
  this release wires it into a proper edit dialog (pencil icon
  next to the trash icon on each row) AND extends the schema with
  `trigger` for full parity with the project-defaults edit
  endpoint shipped in this same release. Backend defends the
  `(user_id, project_id, trigger)` uniqueness invariant with a
  clean 409 on rename collision (race-safe via
  IntegrityError fallback). Pinned by
  `tests/unit/test_user_deliveries_and_sub_edit.py::TestUserSubscriptionTriggerRename`.
- **Edit project default subscriptions in the dashboard.** Same
  shape as the personal-sub fix: edit pencil + unified
  create/edit dialog. Backend `PATCH /api/v1/projects/{slug}/notifications/defaults/{id}`
  was added earlier in this release with full admin-gate + project
  scoping + race-safe uniqueness check; now wired in the UI.
  Pinned by `tests/unit/test_default_subscription_update.py` (7 tests
  covering add-channel-to-existing, partial cooldown, trigger
  rename, trigger collision 409, foreign-channel 409, IDOR 404,
  empty-noop).
- **`GET /api/v1/user/deliveries`** — personal delivery history
  across all of the user's projects. Mirror of the project-scoped
  `/projects/{slug}/notifications/deliveries` audit log, scoped to
  the calling user via a join to `user_subscriptions.user_id`.
  Cursor-paginated (50/page) with optional `?project_slug=` filter
  to narrow to one project. Includes deliveries from projects the
  user is no longer a member of — historical audit data outlives
  membership. The dashboard renders ex-membership rows with a
  "you left this project" badge so the row reads honestly. Pinned
  by `tests/unit/test_user_deliveries_and_sub_edit.py::TestUserDeliveries`
  (6 tests covering cross-project listing, project_slug filter,
  unknown slug, IDOR isolation between users, ex-membership
  visibility, cursor pagination).
- **Filter parity between personal subscriptions and project
  defaults.** The backend `SubscriptionFilters` schema has
  supported `priority`, `task_name_pattern`, and `queue` since
  v1.0.x — but the dashboard exposed only some of them on each
  surface. v1.0.18 adds the missing inputs:
  - **Project defaults dialog** gained `priority`,
    `task_name_pattern`, AND `queue` filter inputs (none of the
    three were previously rendered).
  - **Personal subscription dialog** gained the `queue` filter
    input (priority + task_name_pattern were already there).
  - Both dialogs now show inline help on the priority filter:
    *"only fires for tasks annotated with `@z4j_meta(priority='critical')`"*
    so users understand why the filter silently matches nothing
    if their task code doesn't annotate priority.
- **Personal subscription dialog gained a project-channels
  multi-select.** The backend has accepted `project_channel_ids`
  in `UserSubscriptionUpdate` since v1.0.x, but the personal-sub
  create/edit dialog only rendered the `user_channel_ids`
  picker. Members of a project can now route their personal
  subscriptions through admin-managed shared channels in addition
  to their personal destinations.
- **`DeliveryPublic.project_id` field.** The delivery audit row
  shape now exposes `project_id` so the new personal Delivery
  History tab can group by project and badge ex-membership
  rows. Backwards-compatible additive field; existing dashboard
  consumers ignore the extra key.

### Changed

- **Notification settings reorganized into role-based hubs (Option C).**
  Pre-1.0.18 the dashboard exposed five separate notification routes
  whose ownership was confusing — was "Channels" personal or
  shared? The two channel pages were 1252+1256 lines of nearly
  duplicated UI. v1.0.18 collapses them by ROLE:
  - `/settings/notifications` becomes the **"Global Notifications"**
    hub with three tabs: *My Subscriptions* + *My Channels* +
    *My Delivery History*. Personal scope only.
  - `/projects/{slug}/settings/notifications` becomes the
    **"Project Notifications"** hub (admin-gated) with three
    tabs: *Project Channels* + *Default Subscriptions* + *Delivery
    Log*. Project-scope, admin-only — non-admin members no
    longer see this entry in the project sidebar at all.
  - The five old route URLs (`/settings/channels`, `/projects/{slug}/settings/providers`,
    `/projects/{slug}/settings/defaults`, `/projects/{slug}/settings/deliveries`,
    plus the notion that `/settings/notifications` was just a
    subs list) all permanently redirect to the appropriate
    `?tab=` of the new hubs. Old bookmarks survive.
  - **Zero data-model changes.** Same database tables, same API
    endpoints, same permissions. The reorg is pure UI.
  - The personal hub's empty state now mirrors the project hub's
    empty state for symmetry.
- **`NotificationDeliveryRepository.list_for_project` cursor fix.**
  The encoded next-cursor used to point at the OVERFLOW row
  (the `limit + 1`th fetch), but the WHERE predicate is strict
  `sent_at < cursor` — so each page boundary silently skipped
  one row. v1.0.18 encodes the LAST visible row of the current
  page; page 2 then starts with what was previously the overflow,
  exactly as paging is intended. Same fix applied to the new
  `list_for_user` method. Pre-existing latent bug in the project
  endpoint; nobody had a regression test against it. Pinned by
  `tests/unit/test_user_deliveries_and_sub_edit.py::TestUserDeliveries::test_pagination_cursor`.

### Compatibility

- Backwards compatible. Existing API consumers see no breaking
  changes — every old endpoint URL still works (PATCH gained
  optional fields, DeliveryPublic gained an optional
  `project_id` field).
- Old dashboard bookmarks survive via permanent client-side
  redirects. Five URL patterns now redirect:
  `/settings/channels` → `/settings/notifications?tab=channels`
  `/projects/{slug}/settings/providers` → `/projects/{slug}/settings/notifications?tab=channels`
  `/projects/{slug}/settings/defaults` → `?tab=defaults`
  `/projects/{slug}/settings/deliveries` → `?tab=deliveries`
- Non-admin project members lose visibility of the project
  Notifications sidebar entry (every tab inside is admin-only —
  members manage their notifications from the personal hub).
  This is a deliberate de-clutter, not a permission change.
- No DB migrations. No env changes. Operator action: `pip install
  -U z4j-brain` and restart.

## [1.0.17] - 2026-04-27

### Fixed

- **SQLite default-subscription save with channel ids no longer
  500s.** `POST /api/v1/projects/{slug}/notifications/defaults`
  with a non-empty `project_channel_ids` list raised
  `TypeError: Object of type UUID is not JSON serializable` on
  commit and surfaced as `{"error":"internal_error"}`. The
  `uuid_array()` column adapter fell back to plain SQLAlchemy
  `JSON` on SQLite, which calls `json.dumps` on the bind value -
  and `json.dumps` doesn't know how to serialize `uuid.UUID`.
  Wrapped the SQLite variant in a `TypeDecorator` that converts
  UUIDs to strings on write and back to UUIDs on read, so callers
  see native UUIDs on both Postgres (real `UUID[]`) and SQLite
  (`JSON`-of-strings). Bug present in v1.0.0..v1.0.16; SQLite-only
  - the Postgres path was unaffected. Three columns affected:
  `user_subscriptions.project_channel_ids`,
  `user_subscriptions.user_channel_ids`,
  `project_default_subscriptions.project_channel_ids`. Pinned by
  `tests/unit/test_uuid_array_sqlite.py`. Operator action: `pip
  install -U z4j-brain==1.0.17` and restart - no DB migrations,
  no env changes. Existing rows on SQLite (which would have
  required the bug to never be triggered to exist at all) are
  unaffected; new writes work correctly.

### Compatibility

- Backwards compatible. Postgres deployments see no change.
  SQLite deployments gain working `list[UUID]` columns where they
  previously 500'd. Wheel content delta vs 1.0.16 is just the new
  TypeDecorator + regression test.

## [1.0.16] - 2026-04-27

### Fixed

- **Wheel ships the bundled dashboard SPA again (v1.0.11
  regression).** The 1.0.15 wheel on PyPI was missing the
  `dashboard/dist/` directory entirely, so `pip install z4j-brain
  && z4j-brain serve` returned `{"detail":"Not Found"}` for
  `GET /` on every fresh install. Pure packaging defect: the
  release-split script's rsync `--exclude='dist'` rule was
  unanchored and matched the SPA's bundle directory at
  `backend/src/z4j_brain/dashboard/dist/` along with the intended
  top-level `dist/` build output. Fixed by anchoring the exclude
  to the package root only (`--exclude='/dist'` on rsync, full
  source-path on robocopy, post-copy `rm -rf` on cp). Belt-and-
  suspenders: the release script now refuses to publish a
  z4j-brain wheel that contains fewer than 100 SPA asset entries
  or is missing `dashboard/dist/index.html` - the same
  regression cannot reach PyPI again.
- API endpoints (`/api/v1/*`), `/metrics`, the WebSocket gateway,
  CLI, and migrations were all working correctly in 1.0.15 - only
  the dashboard HTML was missing. **Operator action: `pip install
  -U z4j` (or `-U z4j-brain`) and restart.** No DB migrations,
  no env changes.
- Docker users were unaffected; the Dockerfile copies the SPA
  separately from the pip install.

### Compatibility

- Backwards compatible. Wheel content delta vs 1.0.15 is purely
  the addition of the missing `dashboard/dist/` tree (267 files).

## [1.0.15] - 2026-04-27

### Security

- **mTLS allow-list bypass closed in an internal opt-in gRPC
  service.** The interceptor used `str.lstrip("DNS:")` to strip
  a URI-style prefix that gRPC sometimes embeds in SAN entries.
  `lstrip` takes a SET of characters - so any leading `D`, `N`,
  `S`, or `:` got stripped from the CN itself. A legitimate
  cert with CN `Scheduler-1` would become `cheduler-1` and
  silently fail the allow-list match (locking out a legitimate
  cert); conversely a hostile cert whose mangled CN happened to
  coincide with an allow-list entry would have been accepted.
  Switched to `str.removeprefix` so only the literal `DNS:`
  prefix is stripped. The affected service is dormant by default
  (gated behind an explicit opt-in flag) so no shipping
  deployment is exposed unless the operator has explicitly
  enabled it. Pinned by
  `tests/unit/test_audit_phase2_fixes.py::TestInterceptorRemovePrefix`.

### Performance

- **Replay-worker N+1 batched** - the pending-fires replay
  worker's catch-up logic used to call `schedules_repo.get(...)`
  once per distinct schedule in a replay batch; replaced with a
  single `WHERE id IN (...)` query. 5-schedule batch now issues
  one SELECT instead of five. Pinned by
  `tests/unit/test_audit_phase2_fixes.py::TestApplyCatchUpBatchedLookup`.
- **P-1 batched heartbeat upsert** - replaces N+1 worker upsert
  round-trips per event batch with a single
  `INSERT...ON CONFLICT DO UPDATE`. The N+1 pattern was the
  dominant cost on the hot WebSocket frame path
  (`_handle_heartbeat` fires every 10s per agent connection;
  ~6 concurrent heartbeats was where audit pass 9 / 2026-04-21
  originally caught a `PendingRollbackError` cascade). The new
  bulk path:
  - dedupes `(engine, worker_name)` tuples in the
    `EventIngestor.ingest_batch` accumulator and flushes them
    as one statement;
  - is dialect-aware (Postgres + SQLite ≥3.24 native
    `ON CONFLICT DO UPDATE`; falls back to the original per-row
    path for any other dialect);
  - preserves "no key, no touch" semantics — a heartbeat-only
    batch carrying just `last_heartbeat` + `state` does not
    blank `hostname` / `concurrency` from an earlier
    `worker_details` batch;
  - runs inside a `begin_nested` savepoint so an
    `OperationalError` (deadlock) falls back transparently to
    the original per-row savepointed path. Fast happy path,
    safe slow path, no regression in resilience.
  - `AgentRepository.touch_heartbeat_at(agent_id, when=...)`
    now carries the batch's `max(occurred_at)` so cross-replica
    `now()` skew can't reorder agent liveness.
  - 200-event batch now issues exactly **one** INSERT against
    `workers` (was 200 SELECTs + up to 200 INSERTs/UPDATEs).
    Verified end-to-end against a real WebSocket: 50 events
    from 3 distinct workers collapse to 3 worker rows with
    `last_heartbeat = max(occurred_at)` per worker (delta
    0.000s). 13 new unit tests + 1 statement-count regression
    guard.

### Security

- **SPA catch-all hardening** - any unmatched path under
  `/api/`, `/ws/`, `/metrics`, `/auth/`, `/setup/`, `/healthz`,
  `/.well-known/`, `/openapi.json`, `/docs`, `/redoc`, `/ready`,
  `/live`, or `/assets/` now returns a clean 404 instead of
  serving `index.html`. Pre-1.0.15 a typo'd API URL like
  `/api/v1/typoo` returned the dashboard SPA HTML with a 200
  status, and frontend code choked on `Unexpected token '<'`
  trying to parse HTML as JSON. The catch-all denylist also
  prevents test-time `include_router(...)` calls from being
  shadowed by the SPA fallback.

### Fixed

- **Migration `2026_04_26_0004-scheduler_columns` downgrade is
  now SQLite-safe.** SQLite's `ALTER TABLE DROP COLUMN` does a
  full table rebuild and re-evaluates every remaining
  constraint and default expression - dropping the
  `catch_up` / `source` / `source_hash` / `last_fire_id`
  columns one by one would fail with `no such column: catch_up`
  on the rebuild because of constraint cross-references.
  Wrapped the SQLite path in `op.batch_alter_table` so all
  drops happen in a single coherent rebuild. Postgres path is
  unchanged (native `ALTER TABLE DROP COLUMN` works fine).
  Required for the test_migration downgrade-roundtrip suite
  and any operator who deploys to SQLite for eval and needs to
  roll back.
- **`api/user_notifications.py` raised `ImportError` instead of
  HTTP 403** on certain forbidden paths (the import was
  `ForbiddenError` which doesn't exist in `z4j_brain.errors`).
  Switched to `AuthorizationError` so the 403 envelope
  actually fires. Latent since the personal-channel routes
  shipped; surfaced during the v1.0.15 audit smoke pass.
- **Trigger-schedule route no longer reaches into a private
  attribute** of `CommandDispatcher` to find the brain's
  `Settings` object. The route used
  `getattr(dispatcher, "_settings", None)` - a fragile
  private-API access that would silently break the moment
  `CommandDispatcher.__slots__` or layout changes. Replaced
  with a proper `Depends(get_settings)` injection plus a
  process-wide singleton on `app.state` (cleanly torn down in
  lifespan). Pinned by
  `tests/unit/test_audit_phase2_fixes.py::TestTriggerRouteUsesProperDependency`.

### Added

- **`Settings.disable_spa_fallback`** (default `False`). When
  `True`, `create_app` skips registering the SPA catch-all
  route. Production never sets this; the unit-test fixture
  sets it so tests can `include_router` extra API routes after
  build time without the catch-all shadowing them.
- **`/metrics` regression coverage** - new tests explicitly
  verify 401 without bearer, 401 with wrong bearer, and 200
  with the correct bearer token. Closes the gap that allowed
  v1.0.13 to ship with a stale `metrics_returns_prometheus_text`
  test that asserted 200 unconditionally.
### Compatibility

- Backwards compatible. The P-1 batched-upsert path is
  internally transparent — same write semantics, fewer SQL
  round-trips.
- Schema additions auto-apply on `z4j-brain serve`; no
  operator action required.
- The new SPA catch-all denylist is a behavior CHANGE for
  anyone who was relying on `/api/v1/typo` returning HTML
  (you weren't, but worth noting).

## [1.0.14] - 2026-04-24

### Security (BREAKING)

- **`/metrics` exposed without auth and dev-mode-on-public-bind are no longer possible by default.** Two gates added:

  **1. `Z4J_METRICS_AUTH_TOKEN` is auto-minted on first boot** (already shipped in 1.0.13, see that entry for details). `/metrics` returns 401 unless the bearer token is presented OR `Z4J_METRICS_PUBLIC=1` is set explicitly.

  **2. `z4j serve` REFUSES to start when `Z4J_ENVIRONMENT=dev` AND the bind host is not loopback.** Pre-1.0.14 the brain would happily start in dev mode bound to `0.0.0.0`, silently exposing dev-mode cookies (`Secure: false`, no `__Host-` prefix), no HSTS, and disabled host validation to anyone who could reach the port. The dev-mode `0.0.0.0` combo is now a startup error with an actionable message naming both safe paths. Combined with the new `127.0.0.1` default bind for dev mode, this closes the silent-public-dev footgun.

  **Operator action on upgrade from 1.0.13:**

  If your brain runs behind a reverse proxy (Cloudflare Tunnel / Caddy / nginx / Traefik) — most deployments — set three env vars in your systemd unit (or wherever you set process env):

  ```ini
  [Service]
  Environment=Z4J_ENVIRONMENT=production
  Environment=Z4J_PUBLIC_URL=https://tasks.example.com
  Environment=Z4J_ALLOWED_HOSTS=["tasks.example.com"]
  ```

  Then `sudo systemctl daemon-reload && sudo systemctl restart z4j`. **Existing dashboard sessions will be invalidated** because the cookie name changes from `z4j_session` to `__Host-z4j_session` (browser-enforced isolation in production mode). Users will need to log in again — one-time.

  See [/operations/dev-vs-production/](https://z4j.dev/operations/dev-vs-production/) for the full migration guide including homelab-on-LAN and loopback-only paths.

- **Auto-promote to production when production-shaped config is detected.** If `Z4J_PUBLIC_URL` starts with `https://` AND `Z4J_ALLOWED_HOSTS` is set explicitly, `Z4J_ENVIRONMENT` defaults to `production` instead of `dev`. Operator can still force `Z4J_ENVIRONMENT=dev` to override; explicit env always wins over the default. Removes the silent-leak case where an operator wired up TLS + allow-list but forgot the third env var.

- **Default bind host is now `127.0.0.1` in dev mode** (was `0.0.0.0`). Bare `pip install z4j && z4j serve` no longer exposes anything beyond loopback. To bind publicly, switch to production mode (the auto-promote path is the easiest way).

### Added

- **PagerDuty native channel** (`type: "pagerduty"`). Routes notifications through the PagerDuty Events API v2 with proper severity mapping, dedup keys, and the canonical `events.pagerduty.com/v2/enqueue` endpoint. Configure with `{"integration_key": "<32-char routing key>", "severity_default": "warning", "severity_map": {"agent.offline": "critical", "task.failed": "error"}}`. Built-in defaults map z4j triggers to PD severities so most operators only need to paste the integration key. Full SSRF / DNS-pin protection consistent with the existing webhook + Slack + Telegram dispatchers.
- **Discord native channel** (`type: "discord"`). POSTs Slack-compatible payloads to a Discord incoming webhook. The dispatcher auto-appends `/slack` to the webhook URL so operators paste the canonical URL Discord shows them in Server Settings -> Integrations -> Webhooks. Same SSRF protection as the generic webhook channel.
- **`z4j metrics-token rotate`** CLI subcommand. Mints a fresh `/metrics` bearer token, atomically rewrites `~/.z4j/secret.env` (replacing or appending the `Z4J_METRICS_AUTH_TOKEN=` line), and prints the new token to stdout. Requires a brain restart for the new token to take effect; the command's stderr output reminds the operator to update their Prometheus scrape config first. The existing `z4j metrics-token` (no subcommand) is preserved as `z4j metrics-token show` for backward compatibility.

### Changed

- `z4j metrics-token` now accepts subcommands (`show` and `rotate`). Bare `z4j metrics-token` still prints the token (defaults to `show`), so existing scripts continue to work unchanged.
- **`z4j --version` and `z4j -V` now print the version**, matching the standard Python CLI convention. The bare `z4j` (no subcommand) and `z4j version` paths still work — the new flags are additive. `-v` (lowercase) is intentionally NOT bound so it stays free for a future `--verbose` flag, matching pip / docker / kubectl.

### Security audit pass — 2026-04-26

Following Codex's audit hardening wave (also folded into this release), an independent three-axis audit was run against the v1.0.14 surface plus the rest of the brain (channels / IDOR / N+1 + DoS). Every Critical, High, Medium, and most Low findings are closed in this release. One pure-perf finding (heartbeat handler N+1 — pre-existing through 1.0.5–1.0.13) is deferred to v1.0.15 with dedicated concurrent-load regression tests.

#### Notification audit log secret-leakage hardening (HIGH)

- **`notification_deliveries.error` no longer leaks channel webhook URLs.** Slack / Discord / Telegram URLs embed the secret in the path (`hooks.slack.com/services/T../B../<secret>`, `discord.com/api/webhooks/<id>/<token>`, `api.telegram.org/bot<TOKEN>/...`). When `httpx` raised a timeout / DNS error its `__str__` included the full URL, which got persisted verbatim into `notification_deliveries.error[:1024]` and surfaced via `GET /deliveries` to any project admin — defeating the masking that `_mask_config` applied on `GET /channels`. New `domain.notifications.sanitize.sanitize_audit_text` scrubs the channel's `webhook_url` / `bot_token` / `integration_key` substrings from `error` and `response_body` before either persistence path writes (test-dispatch + real-event dispatch).
- **`notification_deliveries.response_body` no longer stores hostile attacker bytes verbatim.** A malicious webhook target could plant phishing HTML / JS / fake CSRF tokens in its 200 response; the body got stored unsanitized and rendered on the Delivery Log page. Sanitizer strips control characters (`\x00`–`\x1f` except tab/CR/LF), enforces a 2 KB cap, and the dashboard already escapes via React text rendering — defense in depth at the write layer.
- **SSRF probe error strings no longer leak internal-network DNS.** Rejection messages like `"target IP 10.0.0.5 is in blocked range 10.0.0.0/8"` used to let an admin enumerate internal-network DNS records via the audit log. Now collapsed to `"target rejected by policy"` in persisted/returned text; resolved IPs only land in operator-facing structlog. Private IP regex (`10.x`, `172.16-31.x`, `192.168.x`, `169.254.x`, `127.x`) is also masked as `<private-ip>` for any leaked address that escapes the SSRF-pattern collapse.

#### `metrics-token rotate` hardening (MEDIUM)

- **Atomic file creation.** Pre-1.0.14 used `Path.write_text` which created the temp file with the process umask (typically `0o644`) then narrowed to `0o600` via `chmod` — a small race window where any local user could read the new bearer from `~/.z4j/secret.env.rotate-tmp`. Now uses `os.open(tmp, O_WRONLY|O_CREAT|O_EXCL, 0o600)` so the file is created with the right mode from the start. The `chmod` follow-up still fires (defense in depth on systems with weird umask interactions) and now warns to stderr instead of silently swallowing failures.
- **Structured log line on rotate** for ops-team correlation — previously rotates were silent, now they emit a `metrics_token_rotated` info-level log with `secret_env` path + uid.

#### PagerDuty `severity_map` validator hardening (MEDIUM)

- Validator now asserts `severity_map` keys are strings AND match the canonical trigger pattern (`task.failed` / `agent.online` / `test.dispatch` / etc.). Pre-1.0.14 the loop accepted any key type; a stored config with `None` / int / bool keys would crash the dispatcher mid-loop on string ops elsewhere.
- `severity_map` capped at 32 entries — well above z4j's ~6 trigger types with headroom.

#### `clear_deliveries` audit + retention (LOW)

- Every `DELETE /api/v1/projects/{slug}/notifications/deliveries` writes one row to the brain audit log (`notifications.deliveries.clear` action) before the delete. Pre-1.0.14 a rogue admin could silently wipe the delivery audit log to cover the trail of a sensitive test dispatch. Audit row carries actor + deleted count + optional `before` timestamp.
- New `?before=<iso8601>` query parameter on the same endpoint — lets future retention-policy automation delete only rows older than N days without wiping recent debugging history. Default behavior (omit `before`) unchanged.

#### Channel-name snapshot in delivery rows (LOW)

- `notification_deliveries` gains `channel_name` + `channel_type` columns (Alembic `2026_04_26_0005_deliv_snap`). Set at insert time in both the test-dispatch and real-event paths. Pre-1.0.14 the dashboard resolved channel name + type via a live JOIN at read time, which let an admin rename a channel after a sensitive dispatch and retroactively rewrite the audit story. Snapshot is now authoritative; live JOIN remains as a fallback for pre-1.0.14 rows where the snapshot is NULL.

#### Defense-in-depth: import endpoints (LOW)

- `import_from_user` and `import_from_project` now `copy.deepcopy` the source channel's `config` instead of `dict(...)` (shallow copy). Closes a future-aliasing bug class where nested dicts (`headers`, `severity_map`) shared references between source and imported channel — a PATCH on the source could leak into the imported copy via the shared reference if SQLAlchemy's `MutableDict` were ever added to the JSON column.

#### Performance + DoS hardening (HIGH/MEDIUM)

- **`task_name_pattern` ReDoS guard.** `fnmatch` on a hostile pattern like `"a*a*a*a*a*a*a*a*a*a*b"` is catastrophically backtracking on long inputs. The filter runs synchronously per event in the WS frame ingest path — one bad subscription DoSed event ingestion across all tenants on the worker. Pydantic validator now rejects patterns with > 5 wildcards or > 3 character classes; runtime guard in `NotificationService._matches_filters` skips the match (and logs) for stale subscriptions whose stored pattern slips past the validator (defense for pre-1.0.14 rows).
- **DNS resolve in validators wrapped in `asyncio.wait_for(5.0)`.** Previously a slow / black-holed DNS server could pin a REST handler for the OS resolver's full retry budget (~30s). The 5s hard cap is well above legit DNS round-trip; a timeout caches the negative result for the same window so a flood of requests for a bad host doesn't multiply the wait.
- **Notification dispatch detached from WS receive loop.** Pre-1.0.14 each `evaluate_and_dispatch` call awaited up to 16 concurrent HTTP deliveries with a 10s timeout *inside* the agent receive loop — a 50-event burst with email subscriptions could pin the WS frame handler for tens of seconds, dropping the agent's heartbeat clock. Now each evaluation runs in a detached `asyncio.Task` with its own DB session; the FrameRouter holds strong references to defeat GC + logs unhandled exceptions via done-callback. Backpressure cap of 256 in-flight tasks per connection prevents an event flood from spawning unbounded background work.
- **Rate limits on test / import / bulk endpoints.** New per-IP throttle buckets:
  - `channel-test` (20/min) — `/channels/test`, `/channels/{id}/test`, `/user/channels/test`, `/user/channels/{id}/test`
  - `channel-import` (30/min) — `/channels/import_from_user`, `/user/channels/import_from_project`
  - `bulk-action` (10/min) — `/tasks/bulk-delete`, `/commands/bulk-retry`, `/commands/purge-queue`, `/schedules/{id}/trigger`
- **List-endpoint LIMIT caps.** `/agents`, `/workers`, `/channels` now enforce a server-side `limit` (default 500, max 5000) instead of returning the entire project's rows. Defends against unbounded result sets in projects with churning rows.
- **Channel `config` size capped at 16 KiB JSON-serialized** at the pydantic boundary on every channel write path (`ChannelCreate`, `ChannelUpdate`, `ChannelTestRequest`, `UserChannelCreate`, `UserChannelUpdate`). Realistic configs are ~1–4 KiB; 16 KiB is comfortably above legit needs and rejects abusive 1 MiB payloads before they hit the DB.
- **Tasks export ceiling lowered from 5 000 000 → 100 000 rows** (`Z4J_TASKS_EXPORT_MAX_ROWS`). Pre-1.0.14 a single export could materialize hundreds of MB of task rows + their JSONB blobs in Python memory. Streaming-cursor rewrite tracked for v1.1.x.
- **`dashboard_hub` PostgreSQL NOTIFY fan-out tasks held by strong refs + done-callback** — pre-1.0.14 fire-and-forget `asyncio.create_task` could be GC'd mid-flight per asyncio docs, swallowing exceptions silently. Mirrors the pattern already used by `registry/postgres_notify.py`.

#### Bonus: pre-existing bug fix

- **POST `/user/subscriptions` no longer returns HTTP 500 for non-members.** Code imported `ForbiddenError` from `z4j_brain.errors` (no such name); should have been `AuthorizationError`. Every call from a user not in the project raised `ImportError` instead of returning 403. Caught during the audit smoke pass, fixed inline.

## [1.0.13] - 2026-04-24

### Security (BREAKING)

- **`/metrics` is now fail-secure by default.** Previously (1.0.11 / 1.0.12), `Z4J_METRICS_AUTH_TOKEN` was optional - unset meant "serve without auth" and the brain just logged a startup WARNING. Every fresh `pip install z4j && z4j serve` therefore exposed project IDs, queue names, task names, and in-memory-state counters (`z4j_inmemory_state_items`) to anyone who could reach the endpoint. The warning was visible only to the operator reading their service log; production deployments behind reverse proxies (Caddy / nginx / Cloudflare Tunnel) inherited the default and leaked metadata to the internet.

  **New policy:**
  - `Z4J_METRICS_AUTH_TOKEN` is **auto-minted on first boot** and persisted to `~/.z4j/secret.env` alongside `Z4J_SECRET` / `Z4J_SESSION_SECRET`. In-place upgrades from 1.0.12 append the new token to the existing `secret.env` and log a one-time info line.
  - `/metrics` requires `Authorization: Bearer <token>` by default. Unauthenticated requests get HTTP 401 with a detail body that names the fix.
  - Operators who intentionally want unauthenticated scrape (sidecar Prometheus on localhost, trusted LAN) must set `Z4J_METRICS_PUBLIC=1` explicitly. The brain logs a loud WARNING and `z4j doctor` surfaces it as a warning.

  **Operator action on upgrade from 1.0.12:**
  1. `pip install -U z4j && systemctl restart z4j`
  2. The first boot appends `Z4J_METRICS_AUTH_TOKEN=...` to `~/.z4j/secret.env` and logs the fact.
  3. Get the token: `z4j metrics-token`
  4. Update your Prometheus scrape config to include the bearer token:
     ```yaml
     scrape_configs:
       - job_name: z4j
         authorization:
           type: Bearer
           credentials: <paste-token>
         static_configs:
           - targets: ["tasks.example.com:443"]
         scheme: https
     ```
  5. If you run Prometheus on the same host and genuinely want unauthenticated scrape: set `Z4J_METRICS_PUBLIC=1` in the systemd unit (`Environment=Z4J_METRICS_PUBLIC=1`), restart, and accept that `z4j doctor` will flag it.

### Added

- **`z4j metrics-token`** CLI subcommand. Reads `Z4J_METRICS_AUTH_TOKEN` from env or `~/.z4j/secret.env` and prints the value to stdout. Shell-safe so `curl -H "Authorization: Bearer $(z4j metrics-token)" http://localhost:7700/metrics` works out of the box.
- **`Settings.metrics_public`** (env: `Z4J_METRICS_PUBLIC`) - explicit opt-in to unauthenticated `/metrics`. Default False. Mutually exclusive with the auth-token path; when True, the token check is skipped and a loud WARNING is logged.
- **`z4j doctor` Warning 4** fires when `Z4J_METRICS_PUBLIC=1` is set, echoing the risk so operators can't drift into forgetting.

### Changed

- Startup banner now reports the metrics auth state explicitly - `metrics_auth_enabled` (normal), `metrics_public_opt_in` (warning), or `metrics_no_auth_configured` (warning - unreachable on a CLI-launched brain, present for custom bootstrappers).

## [1.0.12] - 2026-04-24

### Fixed

- **`GET /` returned `{"detail": "Not Found"}` after upgrade.** The 1.0.11 wheel on PyPI shipped the Python code without the bundled dashboard SPA (the `backend/src/z4j_brain/dashboard/dist/` directory was missing from the artifact), so the SPA catch-all at `main.py:558` never registered because `dashboard_dir.is_dir()` was false. Every fresh install of 1.0.11 that wasn't running behind a dev-mode ``vite`` got a bare 404 on every dashboard URL. The pre-build step that copies `packages/z4j-brain/dashboard/dist` → `backend/src/z4j_brain/dashboard/dist` is now verified explicitly before each wheel build, and the wheel-manifest check confirms `index.html` + `assets/` + 260+ files land inside the distribution. No code change; pure packaging fix.

### Operator action

`pip install -U z4j-brain` (or `pip install -U z4j`) and restart. No DB migrations, no config changes, no env vars.

## [1.0.11] - 2026-04-24

### Security

- **`/metrics` gained optional bearer-token guard** (`Z4J_METRICS_TOKEN`) + a startup WARNING when unset. Prometheus labels expose project IDs, queue names, and task names - readable by anyone who can reach the endpoint. The startup warning now names the risk and the fix in a single line (audit Medium-1).
- **`Config.model_validator` rejects `transport=longpoll` with empty / invalid `agent_id`.** Every framework adapter now surfaces `Z4J_AGENT_ID` + `Z4J_TRANSPORT` as explicit env reads (audit Medium-2). Prevents long-poll agents from starting without the identity UUID they need to enforce per-agent sequencing on the brain.
- **`PurgeQueueRequest` carries `confirm_token` + `force` fields** so the agent's HMAC-of-`(queue, depth)` check actually fires on purge commands. Before this, the check existed but the fields were missing from the Pydantic model, so FastAPI silently dropped them before validation (audit Medium-3).
- **Schedule commands route via `_pick_scheduler_agent`** (online + scheduler-support) instead of `next(iter(list_for_project))`. An offline agent could be selected and the command would hang until timeout (audit Medium-4).

### Added

- **`Z4J_METRICS_TOKEN` env var** to require `Authorization: Bearer <token>` on `/metrics`. Startup WARNING when unset.
- **Django system check for `Z4J_HMAC_SECRET`** - `manage.py check` fails loudly when the secret is missing (audit Low-1).
- **`clamp_buffer_path` promoted to public helper** in `z4j_bare.storage`. Django / Flask / FastAPI adapters now apply the same root-whitelist clamp the bare `install_agent` entry point uses - closes the gap where a framework-adapter operator could set `Z4J_BUFFER_PATH` outside the `~/.z4j` / `$TMPDIR/z4j-{uid}` roots (audit Low-2).
- **`admin_project_list_cap` + `tasks_export_max_rows`** as tunable Settings, preventing unbounded memory growth for operators with very large project / task sets (audit Low-3).
- **Notification channel test endpoints** - unsaved-config preflight + per-channel dry-run.

### Changed

- Bumped minimum `z4j-core` to `>=1.0.5` (longpoll agent_id validator). Adapter packages also bumped to pick up the promoted `clamp_buffer_path`: `z4j-bare>=1.0.7`, `z4j-django>=1.0.7`, `z4j-flask>=1.0.4`, `z4j-fastapi>=1.0.4`.

## [1.0.10] - 2026-04-24

### Fixed

- **`z4j doctor` raised `NameError: name 'os' is not defined`** because the function used `os.environ.get(...)` without importing `os` in its local scope. Other CLI handlers do their own `import os` and that pattern was missed when `_run_doctor` was added in 1.0.9. Brain-only patch; no behavioural change beyond the command no longer crashing on the second warning probe.

## [1.0.9] - 2026-04-24

### Security

- **`invalid_host` 400 response is now ALWAYS minimal**, regardless of `Z4J_ENVIRONMENT`. Returns only `{error, message, request_id}` to the wire. The dev-mode-verbose gate from 1.0.8 was insufficient: a homelab operator on the SQLite/pip path (defaults to dev mode) exposed via Cloudflare Tunnel / Caddy / nginx still leaked internal LAN IPs, Tailscale node names, and a ready-to-paste `Z4J_ALLOWED_HOSTS=` value to anyone who could reach the brain. Operators get the verbose detail in the operator-facing INFO log (always), correlatable via `request_id`. Audited the rest of the middleware stack (body_size, errors, real_client_ip, request_id, security_headers, ip_rate_limit) - no other leaks.

### Added

- **`z4j backup --output PATH`** - point-in-time database snapshot to a single file. SQLite uses `VACUUM INTO` (online; brain keeps serving). PostgreSQL shells out to `pg_dump -Fc -Z6 --no-owner --no-acl`. Backend auto-detected from `Z4J_DATABASE_URL`.

- **`z4j restore PATH --force`** - restore from a backup file. Brain MUST be stopped. SQLite preserves the existing DB at `<dbpath>.pre-restore-bak` for manual rollback. PostgreSQL uses `pg_restore --clean --if-exists --no-owner --no-acl`.

- **`z4j doctor`** - full health + configuration audit. Composes `check` (config / DB / migrations) with warnings for common pitfalls: dev mode on a non-loopback bind, `Z4J_DEBUG_HOST_ERRORS=1` set, auto-minted secrets needing off-host backup, no users / projects / agents yet. Run before exposing the brain to the internet or before a release.

- **`z4j serve --debug-host-errors`** - opt-in flag that re-enables verbose `invalid_host` response bodies (the 1.0.6/1.0.7 shape with `details.rejected_host`, `details.allowed_hosts`, `details.fix`). Refused outside dev mode by the CLI; refused at runtime by the middleware if `Z4J_ENVIRONMENT != "dev"`. Prints a loud warning at startup. Only safe for local-laptop dev bound to 127.0.0.1.

- **CI: pip-audit + trivy security workflow** on the public z4jdev/z4j-brain repo. Runs on every push, every PR, and daily at 03:00 UTC so new CVEs surface without waiting for a code push. Uploads SARIF to GitHub Security tab.

- **Dashboard: `host_name` is now a top-level column on `/projects/{slug}/agents`** (was a sub-label under the agent name in 1.0.6-1.0.8). Distinguishes the operator-supplied label (`Z4J_AGENT_NAME`) from the mint-time `name`. When the agent did not advertise a host name, the column shows `-`.

- **Operations runbooks** at z4j.dev:
  - [/operations/allowed-hosts/](https://z4j.dev/operations/allowed-hosts/) - the four sources, precedence, file format, security model, troubleshooting.
  - [/operations/backup-restore/](https://z4j.dev/operations/backup-restore/) - SQLite + Postgres playbook, scheduled-backup systemd unit, what else to back up beyond the DB.
  - [/operations/upgrade-rollback/](https://z4j.dev/operations/upgrade-rollback/) - pip + Docker upgrade flow, rollback steps, agent vs brain version skew rules.
  - [/operations/incident-response/](https://z4j.dev/operations/incident-response/) - playbooks for brain down, audit-chain tampered, leaked agent token, lost last admin.

## [1.0.8] - 2026-04-24

### Added

- **Persistent allow-list file at `~/.z4j/allowed-hosts`.** One hostname per line, `#` comments allowed. Read by `z4j serve` on every boot and merged into the auto-detected hostname/IP set. The answer to "where do I put my public DNS name (e.g. `tasks.example.com`) so I don't have to set `Z4J_ALLOWED_HOSTS` or pass `--allowed-host` every time".

- **`z4j allowed-hosts` subcommand** to manage the file from the CLI:

  ```bash
  z4j allowed-hosts add tasks.example.com api.example.com
  z4j allowed-hosts list
  z4j allowed-hosts remove old-name.example.com
  z4j allowed-hosts path
  ```

  All operations are atomic + idempotent. Edits take effect on the next `z4j serve` start.

- **Boot banner now shows persisted file source.** When `~/.z4j/allowed-hosts` is non-empty, the startup output explicitly calls it out so operators can see exactly where each allowed host came from.

### Security

- **`invalid_host` 400 response no longer leaks internal hostnames in non-dev mode.** Previously the rejection payload included `rejected_host`, the full `allowed_hosts` array (with internal LAN IPs, Tailscale node names, hostnames), and a ready-to-paste `Z4J_ALLOWED_HOSTS=...` value - all visible to any unauthenticated HTTP client (web crawlers, attackers probing the surface, accidental scrapers). Mirrors Django's DEBUG-only detailed-error pattern:
  - **dev mode** (default for SQLite/pip path): full detail in the response (the operator IS the HTTP client).
  - **non-dev mode** (Postgres production): minimal body - just `{"error":"invalid_host","message":"Bad Request: invalid Host header.","request_id":"..."}`. The verbose detail is still in the operator-facing INFO log (`journalctl` / docker logs), correlatable via `request_id`. Crawlers and attackers learn nothing about internal infrastructure.

## [1.0.7] - 2026-04-23

### Added

- **Auto-detect LAN and interface IPs on the SQLite/dev path.** 1.0.6 added hostname + FQDN to the default Host allow-list, but missed the homelab case where operators reach the brain via its LAN IP (e.g. `192.168.1.42`) rather than its hostname. 1.0.7 also enumerates:
  - Every IP returned by `socket.gethostbyname_ex(gethostname())` (covers multi-interface boxes with proper `/etc/hosts`).
  - The primary outbound IP via the stdlib UDP-socket trick (picks up the address the OS would use to reach the internet, even on Debian-default setups where the resolver only knows 127.0.1.1).

  Includes Tailscale addresses (`100.x.x.x`) and Docker bridge IPs automatically. Production (Postgres) still requires explicit `Z4J_ALLOWED_HOSTS`.

## [1.0.6] - 2026-04-23

### Added

- **`z4j serve --allowed-host` (repeatable)** for ad-hoc Host-header allow-list additions. Use when reaching the brain via a hostname or IP that the auto-detect missed:

  ```bash
  z4j serve --allowed-host brain.internal.lan --allowed-host 10.0.0.5
  ```

  Merges with `Z4J_ALLOWED_HOSTS` env (env wins), the auto-detected system hostname (see below), and localhost.

- **Auto-detect the server's hostname + FQDN on the SQLite/dev path.** A fresh `pip install z4j && z4j serve` on a remote VM now accepts requests to that hostname out of the box without any `Z4J_ALLOWED_HOSTS` config. Previously the bare-metal pip default was `["localhost","127.0.0.1"]`, so accessing the brain via the server's actual hostname returned `invalid_host` 400. Auto-detect adds `socket.gethostname()` + `socket.getfqdn()` to the dev defaults. Production (Postgres) still requires `Z4J_ALLOWED_HOSTS` explicitly.

- **Boot banner showing the resolved Host: allow-list.** Right after the first-boot setup-token banner, `z4j serve` now prints what it will accept, plus how to add more.

- **Agent `host_name` exposed on the agents API.** The agent's hello frame already carried `host.name` (z4j-bare 1.0.3+, populated from `Z4J_AGENT_NAME` / `settings.Z4J["agent_name"]`). The brain now persists it under `agent_metadata.host` and exposes it as a `host_name` field on `GET /api/v1/projects/{slug}/agents`. Useful when one agent token is shared across multiple workers and you want per-instance labels in the dashboard.

### Fixed

- **`invalid_host` rejection error is now actionable.** Previously the response was a bare `{"error":"invalid_host","details":{}}`. The 400 now includes `rejected_host`, `allowed_hosts`, and a concrete `fix` string showing both the env-var form and the CLI-flag form. Also logs the rejection at INFO so operators see it in `journalctl` / docker logs without curling the response.

- **Dashboard timestamps no longer render as "in 4 hours" for non-UTC operators.** The bug: backend serializes some datetime columns via Python's `datetime.isoformat()` which omits the timezone marker for naive datetimes; the dashboard's `new Date(value)` parser then interpreted the string as local time per ECMA-262, putting a UTC timestamp 4-6 hours into the future for an operator in EDT/CST/PDT. Fix is defensive on the frontend: the new `parseTimestamp` helper appends `Z` to any timestamp string with no timezone marker before parsing. Applies to `formatRelative`, `formatAbsolute`, the trends-chart tick labels, and notification mute states.

- **Dashboard renders `host_name` on the agents page.** When the agent advertised an operator-supplied label via `Z4J_AGENT_NAME`, the brain previously stored it nowhere and the dashboard had no way to show it. Now visible as a "host: &lt;name&gt;" sub-label under the mint-time agent name on `/projects/{slug}/agents` (only when present and different from the mint-time name).

## [1.0.5] - 2026-04-23

### Added

- **`z4j` as the primary CLI command name.** The brain wheel now ships two console scripts that call the same entry point: `z4j` (primary, recommended for new docs) and `z4j-brain` (back-compat alias for users on 1.0.0-1.0.4 documentation). `z4j serve`, `z4j check`, `z4j createsuperuser`, etc. all work exactly like their `z4j-brain` equivalents. The `--help` banner auto-detects which name was invoked and shows matching examples. The shorter name matches the package name on PyPI (`pip install z4j`), the brand (z4j.com / z4j.dev), and reduces friction (3 chars vs 9 chars, no hyphen to fat-finger).
- **Five new operator subcommands** (Django/Flask-style):
  - `z4j reset [--force] [--nuke-secrets]` - wipe every runtime row in the brain DB and put the install back into pre-first-boot state. Refuses without `--force` for safety.
  - `z4j createsuperuser --email X --password-stdin` - alias for `bootstrap-admin` using the Django-familiar verb. Auto-runs migrations on a truly fresh install.
  - `z4j changepassword <email> --password-stdin` - reset a user's password from the CLI; bumps `password_changed_at` so any existing session for that user is invalidated on next request.
  - `z4j check` - non-destructive validation: config loads, DB reachable, alembic at head. Returns 0 on green.
  - `z4j status` - high-level state summary: version, alembic head, environment, DB URL, row counts (users, projects, agents, tasks, sessions, audit rows).
- Help banner restructured: top-level `z4j --help` now includes a "Common flows" cheat-sheet for the most-used commands.

### Fixed

- **Session liveness check incorrectly killed sessions issued in the same wall-clock second as a password change.** SQLite's `func.now()` is second-precision; Python's `datetime.now()` is microsecond-precision; the comparison `issued_at <= password_changed_at` was true within the second, so every session minted by setup-complete or password-change failed the next request with 401. Added a 1-second grace window. SQLite users hit this on every fresh install. Fix in [auth/sessions.py:202](packages/z4j-brain/backend/src/z4j_brain/auth/sessions.py#L202).
- **Dashboard `/login` route didn't check setup-status.** Users navigating directly to `/login` (bookmark, refresh after logout) on a brain with no admin saw the login form, typed credentials, got 401-invalid-credentials forever. Now the route's `beforeLoad` calls `/setup/status` first and hard-redirects to `/setup` when `first_boot=true`. Fix in [dashboard/src/routes/login.tsx](packages/z4j-brain/dashboard/src/routes/login.tsx).
- Dropped the misleading "first time here? run the first-boot setup" link from the login page (redundant with the new beforeLoad redirect; would have only been visible during a tiny race window).
- **Version display reports the brain wheel version** (1.0.5) instead of z4j-core's protocol version (1.0.1). Previously every operator-facing surface (startup banner, `/api/v1/health`, `z4j version`) reported "1.0.1" no matter which brain wheel was installed, because `__version__` re-exported `z4j_core.version.__version__`. Now reads from `importlib.metadata.version("z4j-brain")` with the protocol version still exposed as `protocol_version` for code that needs it. Fix in [backend/src/z4j_brain/__init__.py](packages/z4j-brain/backend/src/z4j_brain/__init__.py).
- **Schema-version-mismatch error now surfaces actionable detail.** When the DB was migrated by a newer brain than the running code, the operator-visible message was just "REFUSING TO START: ... See logs above for details" (with no details actually shown). Now logs the full message: which version is in the DB, which is in the code, what to do.

### Notes

- The `z4j-brain` script name continues to work indefinitely. We may remove it in a future major version once all docs and known installs have migrated, but no removal is scheduled.

## [1.0.4] - 2026-04-23

### Fixed

- **Stale-DB cross-version safety net.** When the bare-metal CLI auto-mints a fresh `Z4J_SECRET` (case 3: no `~/.z4j/secret.env` present), it now also moves any pre-existing `~/.z4j/z4j.db` to `z4j.db.stale-bak` and clears the SQLite WAL/journal sidecars. This fixes a confusing failure mode where an operator who installed an older z4j-brain that crashed mid-bootstrap (DB created by alembic, secret never persisted) and then upgraded would get "invalid_token" errors on every setup attempt - the new install was minting first-boot tokens against a fresh secret, but the audit-log + token-hash chain in the DB was signed under the lost secret.
- **Less-aggressive setup rate limit.** Bumped `first_boot_attempts_per_ip` default from 5 to 30 in the 15-minute window. The original threshold tripped on common operator UX patterns (form typos, stale browser tabs, double-submits) and the only escape was waiting 15 minutes or wiping `~/.z4j/`.
- **Rate-limit lockout no longer self-perpetuates.** Each rate-limit-blocked request used to write a `setup.attempt` audit row, which the budget check then counted, pushing the lockout window forward indefinitely. Removed the audit write on the rate-limit branch (the original failures already in the table maintain the count for the full 15-minute TTL).
- **Actionable error messages on setup failures.** The 404 responses for `no_active_token`, `expired`, and `invalid_token` now include operator-facing guidance ("Restart the brain to mint a fresh setup URL", "This setup link is from a previous server run...", etc.) instead of the opaque "setup token expired or already used".

### Added

- **`z4j-brain reset-setup` CLI command.** Wipes pending first-boot tokens and the `setup.*` audit-log rows so the next `serve` mints a fresh URL from a clean slate. Refuses if a first admin user already exists (security guardrail). For operators stuck in the rate-limit + stale-token loop without wanting to nuke `~/.z4j/`. Use `--force` to skip the prompt.

### Compatibility

- Backwards compatible. Operators who set `Z4J_SECRET`, `Z4J_SESSION_SECRET`, etc. via env vars or compose files are unaffected. The auto-cleanup branch only fires when the brain mints a fresh secret (i.e. when there's no pre-existing `~/.z4j/secret.env`).
- The rate-limit budget bump is a default-value change. Operators who explicitly pinned `Z4J_FIRST_BOOT_ATTEMPTS_PER_IP` see no behavior change.
- Setup error messages still return HTTP 404 + the same `details.reason` codes (`no_active_token`, `expired`, `invalid_token`); only the human-readable `message` changed. UI clients that parse `details.reason` are unaffected.

## [1.0.3] - 2026-04-22

### Fixed

- **`z4j-brain serve` now works zero-config on a fresh install.** Before this fix, `pip install z4j-brain && z4j-brain serve` crashed with a Pydantic `ValidationError: secret + session_secret Field required`. The CLI auto-defaulted `Z4J_DATABASE_URL` to `~/.z4j/z4j.db` but did NOT auto-mint HMAC signing keys, so the operator had to manually export `Z4J_SECRET` and `Z4J_SESSION_SECRET` before the first run. The Docker entrypoint had always done this, but the bare-metal CLI did not.
- The CLI now mirrors the Docker entrypoint: on first boot, mints fresh `Z4J_SECRET` + `Z4J_SESSION_SECRET` via `secrets.token_urlsafe(48)`, persists them to `~/.z4j/secret.env` (chmod 600 on Unix), and reuses them on subsequent boots so sessions, tokens, and the audit-log HMAC chain survive across restarts. Prints a clear warning that this is evaluation mode and operators must set the secrets explicitly for production.
- Also auto-defaults `Z4J_ENVIRONMENT=dev` and `Z4J_ALLOWED_HOSTS=["localhost","127.0.0.1"]` for SQLite mode so the production-mode invariant validators don't reject the dev boot.

### Documentation

- README's "Quick start" rewritten: `pip install z4j-brain && z4j-brain serve` is now the entire flow. The previous instructions had operators manually generating secrets via `secrets.token_urlsafe`, which was friction we should never have shipped.

### Compatibility

- All operator-facing env var contracts are unchanged. Anyone who already sets `Z4J_SECRET`, `Z4J_SESSION_SECRET`, etc. via env vars or compose files sees no behavior change. The auto-mint kicks in only when those env vars are unset.

## [1.0.1] - 2026-04-22

### Removed

- **`[otel]` extra dropped.** The extra installed `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi`, and `opentelemetry-exporter-otlp` packages, but z4j-brain never actually wired them up - no `TracerProvider` initialization, no FastAPI instrumentation, no OTLP export path. The `otel_exporter_endpoint` setting was declared in `Settings` but never read. Shipping an extra that installs packages without using them misrepresents the feature surface. OpenTelemetry integration will return as a real working feature in a future release, at which point the extra will be reintroduced.
- `otel_exporter_endpoint` setting removed from `Settings` (dead code; never referenced by any codepath). Anyone who set `Z4J_OTEL_EXPORTER_ENDPOINT` in their environment is unaffected because `Settings.model_config` uses `extra="ignore"`, so unknown env vars are silently skipped.

### Notes

- **Observability that actually works** in 1.0.1: Prometheus `/metrics` endpoint (8+ Counters and Histograms in `api/metrics.py`, toggleable via `Z4J_METRICS_ENABLED`), structured JSON logs via `structlog` (toggleable via `Z4J_LOG_JSON`). No functional changes from 1.0.0.

## [1.0.0] - 2026-04-21

First public release.

### Features

- **Self-contained wheel.** `pip install z4j-brain` lands a fully working control plane: FastAPI backend + React dashboard + embedded SQLite + Alembic migrations + packaged `alembic.ini`, all in one Python package. No npm, no Docker, no separate database server required.
- **Dashboard.** React 19 + TanStack Start SPA served at `/` from the same process. Covers projects, agents, tasks, commands, schedules, events (SSE live tail), audit log, users, roles, API keys, notification channels (email / Slack / webhook), export jobs, and feature flags.
- **Agent protocol.** WebSocket primary transport with HTTPS long-poll fallback. Signed command dispatch with command-level HMAC signatures.
- **Multi-tenant.** Projects with per-project memberships, role-based permissions (admin / maintainer / viewer), per-project rate limits, per-project event retention, per-project default notification subscriptions.
- **Two database backends.** SQLite (default, via `aiosqlite`) for homelab / small-team; PostgreSQL 18 (via optional `[postgres]` extra) for multi-worker horizontal scale-out. PostgreSQL unlocks `LISTEN/NOTIFY`-based registry fan-out across workers.
- **Observability.** Prometheus `/metrics` endpoint with 8+ Counters and Histograms (events ingested, tasks by state, task duration, commands by state, notifications sent, etc.). Structured JSON logs via structlog.
- **First-boot UX.** Zero-config admin provisioning via signed, single-use, 15-minute setup token printed to stdout, or via `Z4J_BOOTSTRAP_ADMIN_*` env vars for zero-log-exposure deployments.
- **820 unit tests + 376 integration tests** covering the full REST/WebSocket/CLI surface.

### Security

- **Argon2id** password hashing with production-grade cost (t=3, m=64 MiB, p=4). Side-channel-free via `argon2-cffi`.
- **HMAC-chained audit log** with `prev_row_hmac` chaining every row to the previous row's HMAC; tamper-evident append-only timeline.
- **Separate secrets** for session signing (`Z4J_SESSION_SECRET`) and HMAC signing (`Z4J_SECRET`); session compromise does not extend to command signing.
- **Startup-time invariant checks** refuse to boot in `production` unless `allowed_hosts` is set and `public_url` uses `https://`.
- **CSP + HSTS** headers (CSP on HTML responses, HSTS in prod over HTTPS), frame-ancestors deny, X-Content-Type-Options nosniff.
- **Command signatures** mint via HMAC-SHA256 over canonical JSON so a stolen in-transit command cannot be replayed or rewritten.
- **Single-use first-boot token** signed with `Z4J_SECRET`, 15-minute TTL, bound to admin setup flow only.
- **Rate limiting** per-project on command issuance and event ingestion.
- **`secrets.token_urlsafe(32)`** for every token the brain mints (invitations, API keys, password-reset, first-boot).
- **Dependency hygiene.** All direct + transitive deps pinned at currently-shipping security-patched floors, zero CVEs across the Python package graph AND the bundled dashboard (React, TanStack, Vite 8, TypeScript 6).
- **Supply-chain hardening.** pnpm `ignore-scripts=true` with explicit `onlyBuiltDependencies=[]` allowlist prevents arbitrary install-time code execution from any of the ~400 dashboard transitive deps.

### Compatibility

- Python 3.11, 3.12, 3.13, 3.14.
- SQLite bundled by default; PostgreSQL 18+ via `pip install "z4j-brain[postgres]"`.
- Operating-system independent (Linux, macOS, Windows).

## Links

- Repository: <https://github.com/z4jdev/z4j-brain>
- Issues: <https://github.com/z4jdev/z4j-brain/issues>
- PyPI: <https://pypi.org/project/z4j-brain/>

[Unreleased]: https://github.com/z4jdev/z4j-brain/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/z4jdev/z4j-brain/releases/tag/v1.0.0
