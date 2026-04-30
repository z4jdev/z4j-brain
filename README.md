# z4j-brain

[![PyPI version](https://img.shields.io/pypi/v/z4j-brain.svg)](https://pypi.org/project/z4j-brain/)
[![Python](https://img.shields.io/pypi/pyversions/z4j-brain.svg)](https://pypi.org/project/z4j-brain/)
[![License](https://img.shields.io/pypi/l/z4j-brain.svg)](https://github.com/z4jdev/z4j-brain/blob/main/LICENSE)

The z4j brain — server, dashboard, API.

One brain process per environment. Agents (one per worker / app process)
connect over an authenticated WebSocket and stream task, worker, queue,
and schedule events. The dashboard surfaces every event for inspection
and exposes the operator action surface.

## Install

```bash
pip install z4j-brain
z4j-brain serve
```

First boot mints HMAC secrets, runs Alembic migrations, creates a SQLite
database at `~/.z4j/z4j.db`, and prints a one-time setup URL to stderr.
Set `Z4J_DATABASE_URL=postgresql+asyncpg://...` to use Postgres.

## What it ships

- **Dashboard** — projects, agents, workers, queues, tasks, schedules,
  audit log, notifications, members, API keys, settings
- **REST API** — full operator surface; the dashboard is a client of it
- **Operator actions** — retry, cancel, bulk retry, purge queue,
  requeue dead-letter, restart worker, schedule CRUD, manual trigger
- **Authentication** — Argon2id passwords, signed session cookies,
  CSRF tokens, per-project bearer-token API keys, project-scoped RBAC
- **Audit log** — HMAC-chained, tamper-evident; every privileged
  operation persisted with the issuer, target, source IP, and result
- **Notifications** — per-user subscriptions and per-project defaults
  across email / Slack / PagerDuty / Discord / Telegram / webhook
  with cooldown + mute
- **Reconciliation** — background worker reconciles stuck tasks
  against the engine's ground truth (no stale "running" rows after
  a worker crash)
- **Schedules** — periodic / interval / cron / one-shot / solar,
  with per-schedule trigger and an operator *Sync now* button to
  pull a fresh inventory from any connected agent

## Configuration

Every knob is an env var. The most-used ones:

- `Z4J_DATABASE_URL` — `sqlite+aiosqlite:///path` or
  `postgresql+asyncpg://user:pass@host/db`
- `Z4J_SECRET` / `Z4J_SESSION_SECRET` — auto-generated on first boot,
  persisted to `~/.z4j/secret.env`. Rotate via `Z4J_PREVIOUS_SECRETS`.
- `Z4J_BIND_HOST` / `Z4J_BIND_PORT` — defaults `127.0.0.1:7700`
- `Z4J_PUBLIC_URL` — base URL the dashboard serves itself from when
  fronted by a reverse proxy (Caddy / nginx / Cloudflare Tunnel)
- `Z4J_ALLOWED_HOSTS` — Host-header allow-list for production deploys

Full reference at [z4j.dev/reference/env-vars/](https://z4j.dev/reference/env-vars/).

## Documentation

Full docs at [z4j.dev](https://z4j.dev).

## License

AGPL-3.0-or-later — see [LICENSE](LICENSE). Your application code
imports only the Apache-2.0 agent packages and is never AGPL-tainted.

## Links

- Homepage: https://z4j.com
- Documentation: https://z4j.dev
- PyPI: https://pypi.org/project/z4j-brain/
- Issues: https://github.com/z4jdev/z4j-brain/issues
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Security: security@z4j.com (see [SECURITY.md](SECURITY.md))
