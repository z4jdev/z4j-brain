# z4j-brain

**License:** AGPL v3 - commercial license available (contact `licensing@z4j.com`).
**Status:** v1.0.4 (production-ready).

The z4j brain: FastAPI backend + React dashboard in a single Python
package. This is the server half of z4j. Agents connect to it over
WebSocket (or HTTPS long-poll fallback) and users interact with it
through the dashboard served from the same process.

## Quick start - 30 seconds

```bash
pip install z4j-brain
z4j serve
```

That's the whole thing. Open <http://localhost:7700/> and follow the
first-boot setup URL printed to your terminal.

> The CLI ships under two names: `z4j` (primary, recommended) and
> `z4j-brain` (back-compat alias for users on 1.0.0-1.0.4 docs).
> Both call the exact same code path - `z4j serve` and
> `z4j-brain serve` are interchangeable.

The first run automatically:

- Creates `~/.z4j/z4j.db` (SQLite, bundled - no Postgres needed for
  evaluation).
- Mints HMAC signing keys to `~/.z4j/secret.env` so sessions and
  audit-log chaining survive across restarts.
- Runs Alembic migrations to head.
- Boots the FastAPI backend + React dashboard on `:7700`.

Everything is self-contained in the `~/.z4j/` directory. To start
fresh, delete that directory and re-run.

For production, set `Z4J_SECRET`, `Z4J_SESSION_SECRET`, `Z4J_DATABASE_URL`,
`Z4J_PUBLIC_URL`, and `Z4J_ALLOWED_HOSTS` explicitly via env vars and
back up the secret store. See [Configuration](#configuration) below.

## First boot

On first run the brain has no admin account yet, so `z4j serve`
prints a one-time setup URL to stdout:

```
╔══════════════════════════════════════════════════════════════════════╗
║                         z4j first-boot setup                         ║
║                                                                      ║
║ Open this URL in your browser to create the admin:                   ║
║                                                                      ║
║ http://localhost:7700/setup?token=<15-minute signed token>           ║
║                                                                      ║
║ Token expires at: <UTC timestamp>                                    ║
║ Single-use. Restart the brain to generate a new one.                 ║
║ For zero-log-exposure setup, use Z4J_BOOTSTRAP_ADMIN_*.              ║
╚══════════════════════════════════════════════════════════════════════╝
```

Open the URL in your browser to pick a username, email, and password.
After submit, you're redirected to the dashboard at
`http://localhost:7700/`, already logged in as the first admin.

The token is single-use, signed with `Z4J_SECRET`, and expires in 15
minutes. If it expires before you use it, just restart the brain for
a fresh one.

## What you can do from the dashboard

- **Projects** - create projects, invite teammates, configure
  per-project defaults.
- **Agents** - see connected workers, their queues, and their last
  heartbeat.
- **Tasks** - browse live + historical task runs with filters, full
  request/response payloads, and structured exception tracebacks.
- **Commands** - issue signed commands to agents (retry, cancel,
  replay).
- **Schedules** - cron-style and interval schedules that fire tasks
  into queues.
- **Events** - raw event stream with server-sent-events live tail.
- **Audit log** - every auth event + every mutation, HMAC-chained for
  tamper detection.
- **Settings** - users, roles, API keys, notification channels
  (email, Slack, webhook), export jobs, feature flags.

## Configuration

All settings are `Z4J_`-prefixed environment variables. Only two are
required (the HMAC secrets above); everything else has a sane default.

| Setting | Default | Notes |
|---|---|---|
| `Z4J_BIND_HOST` | `0.0.0.0` | ASGI bind address |
| `Z4J_BIND_PORT` | `7700` | ASGI bind port |
| `Z4J_PUBLIC_URL` | `http://localhost:7700` | Used to build first-boot + password-reset links |
| `Z4J_DATABASE_URL` | bundled SQLite | `postgresql+asyncpg://...` for PostgreSQL |
| `Z4J_ENVIRONMENT` | `production` | Set to `dev` to relax host-header + HTTPS checks |
| `Z4J_EVENT_RETENTION_DAYS` | `30` | How long raw events live before pruning |
| `Z4J_AUDIT_RETENTION_DAYS` | `90` | How long audit-log rows live |
| `Z4J_COMMAND_TIMEOUT_SECONDS` | `60` | Pending commands past this are marked timed-out |
| `Z4J_AGENT_OFFLINE_TIMEOUT_SECONDS` | `30` | Heartbeats older than this mark agent offline |
| `Z4J_RATELIMIT_COMMANDS_PER_MINUTE` | `100` | Per-project command issuance cap |
| `Z4J_RATELIMIT_EVENTS_PER_SECOND` | `10000` | Per-project event ingestion cap |
| `Z4J_FIRST_BOOT_TOKEN_TTL_SECONDS` | `900` | First-boot setup token lifetime |
| `Z4J_ARGON2_TIME_COST` | `3` | Argon2id password hashing time cost |
| `Z4J_ARGON2_MEMORY_COST` | `65536` | Argon2id memory cost (KiB, default 64 MiB) |
| `Z4J_METRICS_ENABLED` | `true` | Expose Prometheus `/metrics` |
| `Z4J_LOG_JSON` | `true` | JSON logs in prod, set `false` for human-readable dev logs |

**Production checklist**: in `production` environment the brain
refuses to start unless `Z4J_ALLOWED_HOSTS` is set and
`Z4J_PUBLIC_URL` uses `https://`. This prevents host-header injection
and insecure reset links.

For the complete list (30+ additional tunables for retention,
observability, CORS, session cookies, registry backend, etc.),
see <https://z4j.dev>.

## PostgreSQL (optional)

If you want PostgreSQL instead of the default SQLite, install the
`postgres` extra and point `Z4J_DATABASE_URL` at your database:

```bash
pip install "z4j-brain[postgres]"
export Z4J_DATABASE_URL=postgresql+asyncpg://user:pass@host/z4j
z4j migrate upgrade head
z4j serve
```

PostgreSQL unlocks the `LISTEN/NOTIFY`-based multi-worker registry
for horizontal scale-out. SQLite keeps everything in a single
process, which is the right choice for homelab and small-team
deployments.

## Licensing

This package is **AGPL v3**. If you run a modified copy as a network
service, you must publish the modifications under the same license.
If your organization's policy forbids AGPL code, a commercial license
is available - contact `licensing@z4j.com`.

All **agent packages** (`z4j-core`, `z4j-bare`, `z4j-django`,
`z4j-celery`, `z4j-rq`, `z4j-dramatiq`, `z4j-huey`, `z4j-arq`,
`z4j-taskiq`, `z4j-apscheduler`, and the rest of the adapter
family) are **Apache 2.0** and can be freely imported into
proprietary code. The split is deliberate: the brain (control
plane) is AGPL, every agent (client library) is Apache. Integrating
z4j into a proprietary app does not subject your app to the AGPL.

## Documentation

Complete documentation, tutorials, API reference, deployment
guides (including Docker Compose presets, reverse-proxy examples,
and production hardening): <https://z4j.dev>.

- Source: <https://github.com/z4jdev/z4j-brain>
- Issues: <https://github.com/z4jdev/z4j-brain/issues>
- Changelog: <https://github.com/z4jdev/z4j-brain/blob/main/CHANGELOG.md>
