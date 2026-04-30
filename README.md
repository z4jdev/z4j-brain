# z4j-brain

[![PyPI version](https://img.shields.io/pypi/v/z4j-brain.svg)](https://pypi.org/project/z4j-brain/)
[![Python](https://img.shields.io/pypi/pyversions/z4j-brain.svg)](https://pypi.org/project/z4j-brain/)
[![License](https://img.shields.io/pypi/l/z4j-brain.svg)](https://github.com/z4jdev/z4j-brain/blob/main/LICENSE)

The z4j brain. Server, dashboard, API, audit log. The control plane
for every Python task queue you run.

One brain process per environment. Agents (one per worker / app
process) connect over an authenticated WebSocket and stream task,
worker, queue, and schedule events. The dashboard surfaces every
event for inspection and exposes the operator action surface;
operators retry, cancel, bulk-retry, purge, restart workers, and
edit schedules from a real UI without SSH or kubectl. Every
privileged action is recorded in an HMAC-chained tamper-evident
audit log. Every endpoint sits behind project-scoped RBAC.

## What makes z4j-brain different

z4j-brain is not a Celery viewer. It is a control plane built around
three propositions:

- **A unified action surface across every Python task engine.** The
  same Retry / Cancel / Bulk-retry / Purge / Restart workflow that
  your operator uses on a Celery task also works on an RQ job, a
  Dramatiq message, a Huey task, an arq job, or a TaskIQ task. One
  dashboard, one mental model, six engines.
- **A real audit story.** Every operator action lands in an
  HMAC-chained audit chain that an auditor can walk linearly to
  prove no row was tampered with. Compliance reviews routinely ask
  "who triggered this?" and the brain answers with the user, the
  source IP, the timestamp, and the action's input + result.
- **Reconciliation.** A background worker reconciles tasks against
  the engine's ground truth on a continuous cadence. No more stale
  "running" rows after a worker SIGKILL, no more orphaned
  "pending" tasks the broker already discarded.

## What it ships

- **Dashboard.** Projects, agents, workers, queues, tasks,
  schedules, audit log, notifications, members, API keys, settings.
  Real-time WebSocket streaming.
- **REST API.** The dashboard is a client of it. Every action is
  scriptable and CI-friendly.
- **Operator actions.** Retry, cancel, bulk retry, purge queue,
  requeue dead-letter, restart worker, schedule CRUD, manual
  trigger. Each routes through the right adapter for the engine
  the task ran on.
- **Authentication.** Argon2id passwords, signed session cookies,
  CSRF tokens, per-project bearer-token API keys, project-scoped
  RBAC (Viewer / Operator / Admin / global brain Admin).
- **Audit log.** HMAC-chained, tamper-evident. Every privileged
  operation persisted with the issuer, target, source IP, and
  result. Exportable to CSV / JSON / xlsx.
- **Notifications.** Per-user subscriptions and per-project defaults
  across email / Slack / PagerDuty / Discord / Telegram / webhook,
  with cooldown, mute, priority filters, and a personal delivery
  log.
- **Reconciliation.** Background worker reconciles stuck tasks
  against the engine's ground truth (no stale "running" rows after
  a worker crash, no orphaned "pending" tasks the broker already
  acked).
- **Schedules.** Periodic / interval / cron / one-shot / solar,
  with per-schedule trigger and an operator *Sync now* button to
  pull a fresh inventory from any connected agent. Pair with
  [`z4j-scheduler`](https://github.com/z4jdev/z4j-scheduler) when
  you want one canonical scheduler across mixed engines.
- **First-class multi-engine.** A single project can run Celery +
  RQ + arq side by side. The brain renders the appropriate badges,
  routes operator actions to the right adapter, and keeps the
  audit log uniform.

## Install

Pip-only (SQLite, single Python process):

```bash
pip install z4j-brain
z4j-brain serve
```

Pip + Postgres (production):

```bash
pip install 'z4j-brain[postgres]'
Z4J_DATABASE_URL='postgresql+asyncpg://user:pass@host/db' z4j-brain serve
```

Docker (single container, SQLite or Postgres via env):

```bash
docker run -d -p 7700:7700 \
  -e Z4J_DATABASE_URL=... \
  -e Z4J_SECRET=... \
  -e Z4J_SESSION_SECRET=... \
  z4jdev/z4j:latest
```

First boot mints HMAC secrets, runs Alembic migrations, creates the
SQLite database at `~/.z4j/z4j.db` (if no `Z4J_DATABASE_URL` set),
and prints a one-time setup URL to stderr that creates the first
admin user.

## CLI surface

Every command runs against the same Settings the server uses, so
you can use it for scripting, smoke tests, and incident response:

```bash
z4j check              # validate config + DB + alembic head
z4j status             # current-state summary (counts)
z4j doctor             # full health audit
z4j audit verify       # walk the HMAC chain end-to-end
z4j backup / restore   # SQLite VACUUM INTO or pg_dump
z4j createsuperuser    # provision an admin
z4j changepassword     # reset a user
z4j upgrade            # check / apply package upgrades from PyPI
```

Full reference at [z4j.dev/reference/cli/](https://z4j.dev/reference/cli/).

## Configuration

Every knob is an env var prefixed `Z4J_`. The most-used ones:

- `Z4J_DATABASE_URL`. `sqlite+aiosqlite:///path` or
  `postgresql+asyncpg://user:pass@host/db`.
- `Z4J_SECRET` / `Z4J_SESSION_SECRET`. Auto-generated on first boot,
  persisted to `~/.z4j/secret.env`. Rotate via `Z4J_PREVIOUS_SECRETS`.
- `Z4J_BIND_HOST` / `Z4J_BIND_PORT`. Defaults `127.0.0.1:7700`.
- `Z4J_PUBLIC_URL`. Base URL the dashboard serves itself from
  when fronted by a reverse proxy (Caddy / nginx / Cloudflare
  Tunnel).
- `Z4J_ALLOWED_HOSTS`. Host-header allow-list for production
  deploys.
- `Z4J_VERSION_CHECK_URL`. Source URL for the operator-initiated
  *Check for updates* button. Defaults to GitHub raw, set empty to
  hide the button (no automatic polling, ever).

Full reference at [z4j.dev/reference/env-vars/](https://z4j.dev/reference/env-vars/).

## Documentation

Full docs at [z4j.dev](https://z4j.dev).

## License

AGPL-3.0-or-later, see [LICENSE](LICENSE). Your application code
imports only the Apache-2.0 agent packages
([`z4j-django`](https://pypi.org/project/z4j-django/),
[`z4j-flask`](https://pypi.org/project/z4j-flask/),
[`z4j-fastapi`](https://pypi.org/project/z4j-fastapi/),
[`z4j-bare`](https://pypi.org/project/z4j-bare/),
plus the engine + scheduler adapters) and is never AGPL-tainted.
Commercial licenses available; contact licensing@z4j.com.

## Links

- Homepage: https://z4j.com
- Documentation: https://z4j.dev
- PyPI: https://pypi.org/project/z4j-brain/
- Issues: https://github.com/z4jdev/z4j-brain/issues
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Security: security@z4j.com (see [SECURITY.md](SECURITY.md))
