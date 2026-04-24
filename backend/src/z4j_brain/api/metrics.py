"""``/metrics`` Prometheus scrape endpoint.

Exposes application-level counters, gauges, and histograms for
Grafana dashboards. The endpoint is mounted at the root (NOT
under ``/api/v1``) so Prometheus scrape configs use a stable path.

Authorization: optional bearer-token guard. Set
``Z4J_METRICS_AUTH_TOKEN`` and the endpoint requires
``Authorization: Bearer <token>``; leave it unset to keep the
legacy "open" behaviour, with a boot-time warning reminding the
operator to either set a token or block ``/metrics`` at the
reverse proxy (Caddy / nginx). Audit 2026-04-24 Medium-1.

Metric naming follows the Prometheus convention:
``z4j_{component}_{metric}_{unit}``.

These metrics are designed to be compatible with common Grafana
dashboard patterns used by Flower and Celery monitoring setups.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from z4j_brain.api.deps import get_settings

if TYPE_CHECKING:
    from z4j_brain.settings import Settings

router = APIRouter(tags=["metrics"])

# ---------------------------------------------------------------------------
# Metric registry
# ---------------------------------------------------------------------------
#
# Brain-private registry so tests can construct multiple create_app()
# instances without "metric already registered" exceptions.

registry = CollectorRegistry()

# -- Events --

z4j_events_ingested_total = Counter(
    "z4j_events_ingested_total",
    "Total events ingested from agents.",
    labelnames=("project", "engine", "kind"),
    registry=registry,
)

# -- Tasks --

z4j_tasks_total = Counter(
    "z4j_tasks_total",
    "Total tasks observed (by final state).",
    labelnames=("project", "task_name", "state"),
    registry=registry,
)

z4j_task_duration_seconds = Histogram(
    "z4j_task_duration_seconds",
    "Task execution duration in seconds.",
    labelnames=("project", "task_name"),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
    registry=registry,
)

# -- Commands --

z4j_commands_total = Counter(
    "z4j_commands_total",
    "Total commands dispatched to agents.",
    labelnames=("project", "action", "status"),
    registry=registry,
)

#: Counter for command results that arrived AFTER the command's
#: row had already transitioned to a terminal state (almost
#: always: the timeout sweeper marked it TIMEOUT before the
#: agent's late ``command_result`` arrived). Operators can graph
#: this against ``z4j_commands_total`` to spot
#: ``command_timeout_seconds`` mis-tuning. R3 finding M7.
z4j_command_late_results_total = Counter(
    "z4j_command_late_results_total",
    "Command results that arrived after the row was already terminal "
    "(usually because timeout_sweeper won the race).",
    labelnames=("status",),
    registry=registry,
)

#: Gauge for in-memory state held by the brain process - sessions
#: in the long-poll signer registry, throttle entries, dashboard
#: subscriptions, etc. Lets operators see brain-restart drops
#: instead of guessing (R3 finding M8). Subsystems register a
#: zero-arg callable via :func:`register_inmemory_subsystem`; the
#: gauge is sampled at scrape time.
z4j_inmemory_state_items = Gauge(
    "z4j_inmemory_state_items",
    "Items held in process-local in-memory state by subsystem.",
    labelnames=("subsystem",),
    registry=registry,
)

_inmemory_subsystems: dict[str, "Callable[[], int]"] = {}


def register_inmemory_subsystem(name: str, count_fn: "Callable[[], int]") -> None:
    """Register a subsystem to be reflected in
    ``z4j_inmemory_state_items{subsystem=name}``.

    The callable is invoked at every Prometheus scrape; it should
    be cheap (read a dict ``len()``, not a DB query).
    """
    _inmemory_subsystems[name] = count_fn


def _refresh_inmemory_gauges() -> None:
    for name, fn in _inmemory_subsystems.items():
        try:
            z4j_inmemory_state_items.labels(subsystem=name).set(fn())
        except Exception:  # noqa: BLE001
            record_swallowed("metrics", f"inmemory_{name}")

# -- Agents and workers --

z4j_agents_online = Gauge(
    "z4j_agents_online",
    "Number of agents currently connected.",
    labelnames=("project",),
    registry=registry,
)

z4j_workers_online = Gauge(
    "z4j_workers_online",
    "Number of workers currently online.",
    labelnames=("project",),
    registry=registry,
)

# -- Queues --

z4j_queue_depth = Gauge(
    "z4j_queue_depth",
    "Number of pending messages in a queue.",
    labelnames=("project", "queue", "engine"),
    registry=registry,
)

# -- WebSocket --

z4j_ws_connections = Gauge(
    "z4j_ws_connections",
    "Number of live WebSocket connections held by this worker.",
    registry=registry,
)

# -- Database pool --

z4j_db_pool_size = Gauge(
    "z4j_db_pool_size",
    "Configured size of the SQLAlchemy connection pool.",
    registry=registry,
)

z4j_db_pool_checked_out = Gauge(
    "z4j_db_pool_checked_out",
    "Number of pool connections currently checked out.",
    registry=registry,
)

# -- Notifications --

z4j_notifications_sent_total = Counter(
    "z4j_notifications_sent_total",
    "Total notification deliveries attempted.",
    labelnames=("project", "channel_type", "status"),
    registry=registry,
)

z4j_notifications_cooldown_skipped_total = Counter(
    "z4j_notifications_cooldown_skipped_total",
    "Number of subscription dispatches skipped because the cooldown "
    "window had not elapsed (the conditional UPDATE returned no rows).",
    labelnames=("project", "trigger"),
    registry=registry,
)

# -- Reliability: intentional exception swallows --
#
# The brain has a small set of sites where a broad exception catch
# is the right call (WebSocket close during shutdown, Prometheus
# metric updates, asyncpg teardown) because the alternative is
# propagating a shutdown-time failure that the caller has no way
# to act on. Every such site increments this counter so a spike is
# visible in Grafana even though the individual call logged at
# debug level. Labelled by module so operators can pinpoint which
# subsystem is degrading.
z4j_swallowed_exceptions_total = Counter(
    "z4j_swallowed_exceptions_total",
    "Intentional exception swallows at I/O boundaries (metric "
    "updates, WebSocket close during shutdown, etc.). A sustained "
    "non-zero rate signals a subsystem in trouble even when no "
    "error-level log line fires.",
    labelnames=("module", "site"),
    registry=registry,
)


def record_swallowed(module: str, site: str) -> None:
    """Best-effort counter bump, itself catching any bookkeeping
    failure. Used from ``except Exception: pass`` sites so ops gets
    a signal without the caller having to think about import order
    or registry-not-initialised races.
    """
    try:
        z4j_swallowed_exceptions_total.labels(module=module, site=site).inc()
    except Exception:  # noqa: BLE001
        # The counter infra itself is broken; nothing sensible to do.
        return


def _check_metrics_auth(request: Request, settings: "Settings") -> None:
    """Enforce the optional bearer-token guard for ``/metrics``.

    When ``settings.metrics_auth_token`` is unset we serve without
    auth (backwards-compatible). When it is set we require the
    exact token in ``Authorization: Bearer <token>`` - constant-time
    compared so failed probes don't leak the token length.
    """
    expected = settings.metrics_auth_token
    if expected is None:
        return
    header = request.headers.get("authorization", "")
    scheme, _, supplied = header.partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        raise HTTPException(
            status_code=401,
            detail="metrics: authorization required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not hmac.compare_digest(
        supplied.encode("utf-8"),
        expected.get_secret_value().encode("utf-8"),
    ):
        raise HTTPException(
            status_code=401,
            detail="metrics: invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get("/metrics", response_class=Response)
async def metrics_endpoint(
    request: Request,
    settings: "Settings" = Depends(get_settings),
) -> Response:
    """Render the brain's metrics in Prometheus text format.

    Refreshes lazy in-memory state gauges before rendering so a
    Prometheus scrape gets a fresh ``z4j_inmemory_state_items``
    snapshot without forcing every subsystem to update on every
    mutation (R3 finding M8).
    """
    _check_metrics_auth(request, settings)
    _refresh_inmemory_gauges()
    body = generate_latest(registry)
    return Response(content=body, media_type=CONTENT_TYPE_LATEST)


__all__ = [
    "record_swallowed",
    "register_inmemory_subsystem",
    "registry",
    "router",
    "z4j_agents_online",
    "z4j_command_late_results_total",
    "z4j_commands_total",
    "z4j_db_pool_checked_out",
    "z4j_db_pool_size",
    "z4j_events_ingested_total",
    "z4j_inmemory_state_items",
    "z4j_notifications_cooldown_skipped_total",
    "z4j_notifications_sent_total",
    "z4j_queue_depth",
    "z4j_swallowed_exceptions_total",
    "z4j_task_duration_seconds",
    "z4j_tasks_total",
    "z4j_workers_online",
    "z4j_ws_connections",
]
