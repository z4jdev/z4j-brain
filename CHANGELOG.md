# Changelog

All notable changes to `z4j-brain` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
