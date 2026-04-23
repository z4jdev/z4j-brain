# Changelog

All notable changes to `z4j-brain` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
