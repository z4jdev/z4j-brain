"""FastAPI application factory.

The single ``create_app`` callable is what uvicorn imports::

    uvicorn z4j_brain.main:create_app --factory --host 0.0.0.0 --port 8080

Construction is split into discrete steps so each one can be
exercised independently in tests:

1. Resolve :class:`Settings` (from env, or supplied by the test)
2. Configure logging
3. Build the database engine + statement-timeout event hooks
4. Build the singletons (hasher, audit, auth, setup, ingestor,
   dispatcher, registry, worker supervisor)
5. Wire middleware (outermost first)
6. Mount routers + WebSocket gateway
7. Lifespan: first-boot check + start registry + start worker
   supervisor + dispose engine on shutdown
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator
from uuid import UUID

import httpx
import structlog
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from z4j_brain import __version__
from z4j_brain.api import (
    auth,
    health,
    setup,
)
from z4j_brain.api import agent_longpoll as agent_longpoll_api
from z4j_brain.api import agents as agents_api
from z4j_brain.api import api_keys as api_keys_api
from z4j_brain.api import audit as audit_api
from z4j_brain.api import commands as commands_api
from z4j_brain.api import events as events_api
from z4j_brain.api import home as home_api
from z4j_brain.api import invitations as invitations_api
from z4j_brain.api import memberships as memberships_api
from z4j_brain.api import metrics as metrics_api
from z4j_brain.api import notifications as notifications_api
from z4j_brain.api import projects as projects_api
from z4j_brain.api import queues as queues_api
from z4j_brain.api import schedules as schedules_api
from z4j_brain.api import stats as stats_api
from z4j_brain.api import tasks as tasks_api
from z4j_brain.api import trends as trends_api
from z4j_brain.api import user_notifications as user_notifications_api
from z4j_brain.api import users as users_api
from z4j_brain.api import workers as workers_api
from z4j_brain.auth.ip import TrustedProxyResolver
from z4j_brain.auth.passwords import PasswordHasher
from z4j_brain.domain.audit_service import AuditService
from z4j_brain.domain.auth_service import AuthService
from z4j_brain.domain.command_dispatcher import CommandDispatcher
from z4j_brain.domain.event_ingestor import EventIngestor
from z4j_brain.domain.setup_service import SetupService
from z4j_brain.domain.workers import (
    AgentHealthWorker,
    CommandTimeoutWorker,
    PeriodicWorker,
    WorkerSupervisor,
)
from z4j_brain.domain.workers.agent_hygiene import AgentHygieneWorker
from z4j_brain.domain.workers.partition_creator import PartitionCreatorWorker
from z4j_brain.domain.workers.pending_fires import PendingFiresReplayWorker
from z4j_brain.domain.workers.reconciliation import ReconciliationWorker
from z4j_brain.domain.workers.schedule_circuit_breaker import (
    ScheduleCircuitBreakerWorker,
    ScheduleFiresPruneWorker,
)
from z4j_brain.logging_config import configure_logging
from z4j_brain.middleware import (
    BodySizeLimitMiddleware,
    ErrorMiddleware,
    HostValidationMiddleware,
    RealClientIPMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from z4j_brain.persistence.database import (
    DatabaseManager,
    create_engine_from_settings,
)
from z4j_brain.persistence.statement_timeout import install_statement_timeouts
from z4j_brain.settings import Settings
from z4j_brain.startup import run_first_boot_check
from z4j_brain.startup_version import SchemaVersionError, check_and_update_schema_version
from z4j_brain.websocket import gateway as ws_gateway
from z4j_brain.websocket import dashboard_gateway as ws_dashboard_gateway
from z4j_brain.websocket.dashboard_hub import (
    DashboardHub,
    LocalDashboardHub,
)
from z4j_brain.websocket.registry import (
    BrainRegistry,
    LocalRegistry,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = structlog.get_logger("z4j.brain.main")


def create_app(
    settings: Settings | None = None,
    *,
    engine: "AsyncEngine | None" = None,
) -> FastAPI:
    """Build the FastAPI app."""
    settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    db_engine = engine or create_engine_from_settings(settings)
    install_statement_timeouts(db_engine, settings=settings)
    db = DatabaseManager(db_engine)

    # Singletons that depend only on settings.
    from z4j_core.redaction import RedactionConfig, RedactionEngine

    hasher = PasswordHasher(settings)
    audit_service = AuditService(settings)
    auth_service = AuthService(
        settings=settings, hasher=hasher, audit=audit_service,
    )
    setup_service = SetupService(
        settings=settings, hasher=hasher, audit=audit_service,
        db_manager=db,
    )
    redaction = RedactionEngine(
        RedactionConfig(
            extra_key_patterns=tuple(settings.cors_origins[:0]),  # placeholder
            extra_value_patterns=(),
            default_patterns_enabled=True,
            max_value_bytes=settings.max_payload_size_bytes,
        ),
    )
    ingestor = EventIngestor(redaction)
    proxy_resolver = TrustedProxyResolver(settings.trusted_proxies)

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------
    # ``deliver_local`` is what the registry calls from the worker
    # that owns the WebSocket. It loads the command row, signs the
    # frame, pushes it to the WS, and marks the row dispatched.
    async def deliver_local(command_id: UUID, ws: WebSocket) -> bool:
        from z4j_brain.persistence.repositories import CommandRepository

        async with db.session() as session:
            commands = CommandRepository(session)
            command = await commands.get_for_dispatch(command_id)
            if command is None:
                return False
            try:
                await ws_gateway.deliver_command_frame(
                    websocket=ws,
                    settings=settings,
                    command=command,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "z4j main: deliver_command_frame crashed",
                    command_id=str(command_id),
                )
                return False
            await commands.mark_dispatched(command_id)
            await session.commit()
        return True

    registry: BrainRegistry
    if settings.registry_backend == "local":
        registry = LocalRegistry(deliver_local=deliver_local)
    else:
        from z4j_brain.websocket.registry.postgres_notify import (
            PostgresNotifyRegistry,
        )

        registry = PostgresNotifyRegistry(
            settings=settings,
            db=db,
            dsn_provider=lambda: settings.database_url,
            deliver_local=deliver_local,
        )

    # Dashboard fan-out hub. Same backend toggle as the agent
    # registry - if you're running multi-worker you want
    # postgres_notify on both, if you're running single-worker the
    # local hub is fine for both.
    dashboard_hub: DashboardHub
    if settings.registry_backend == "local":
        dashboard_hub = LocalDashboardHub()
    else:
        from z4j_brain.websocket.dashboard_hub.postgres_notify import (
            PostgresNotifyDashboardHub,
        )

        dashboard_hub = PostgresNotifyDashboardHub(
            settings=settings,
            db=db,
            dsn_provider=lambda: settings.database_url,
        )

    # CommandDispatcher needs the registry, AuditService, settings,
    # and (optional) the dashboard hub so command-issuing routes
    # can fan out a command.changed topic after committing.
    command_dispatcher = CommandDispatcher(
        settings=settings,
        registry=registry,
        audit=audit_service,
        dashboard_hub=dashboard_hub,
    )

    # ------------------------------------------------------------------
    # Optional: z4j-scheduler gRPC service
    # ------------------------------------------------------------------
    # Constructed unconditionally so the .stop() in lifespan teardown
    # is symmetric, but ``.start()`` short-circuits when
    # ``scheduler_grpc_enabled=False`` so the gRPC runtime is never
    # imported in installs that don't use it.
    scheduler_grpc_server: Any = None  # type: ignore[assignment]
    if settings.scheduler_grpc_enabled:
        try:
            from z4j_brain.scheduler_grpc.server import (  # noqa: PLC0415
                SchedulerGrpcServer,
            )

            scheduler_grpc_server = SchedulerGrpcServer(
                settings=settings,
                db=db,
                command_dispatcher=command_dispatcher,
                audit_service=audit_service,
            )
        except ImportError:
            # Optional extra not installed - log loudly and continue.
            # Operator either installs `z4j[scheduler-grpc]` or
            # unsets Z4J_SCHEDULER_GRPC_ENABLED.
            logger.warning(
                "z4j brain: Z4J_SCHEDULER_GRPC_ENABLED is set but the "
                "scheduler-grpc extra is not installed. Run "
                "`pip install z4j[scheduler-grpc]` to enable.",
            )

    # Background workers. Always-on workers are unconditional;
    # z4j-scheduler-related workers are gated behind
    # ``Z4J_SCHEDULER_GRPC_ENABLED`` so default installs don't pay
    # connection-pool slots for a feature they're not using. v1.0.18
    # shipped the three scheduler workers unconditionally and
    # operators saw agents flap to "offline" because the heartbeat
    # path couldn't get a slot. v1.0.19 gates them.
    _workers: list[PeriodicWorker] = [
        PeriodicWorker(
            name="command_timeout_worker",
            tick=CommandTimeoutWorker(db).tick,
            interval_seconds=float(settings.command_timeout_sweep_seconds),
        ),
        PeriodicWorker(
            name="agent_health_worker",
            tick=AgentHealthWorker(db=db, settings=settings).tick,
            interval_seconds=float(settings.agent_health_sweep_seconds),
        ),
        PeriodicWorker(
            name="agent_hygiene_worker",
            tick=AgentHygieneWorker(db=db, settings=settings).tick,
            interval_seconds=float(settings.agent_hygiene_sweep_seconds),
        ),
        PeriodicWorker(
            name="reconciliation_worker",
            tick=ReconciliationWorker(
                db,
                stale_threshold_seconds=(
                    settings.reconciliation_stale_threshold_seconds
                ),
                dispatcher=command_dispatcher,
            ).tick,
            interval_seconds=float(
                settings.reconciliation_sweep_seconds,
            ),
        ),
        PeriodicWorker(
            name="partition_creator_worker",
            tick=PartitionCreatorWorker(db=db, settings=settings).tick,
            interval_seconds=3600.0,  # hourly
        ),
    ]
    if settings.scheduler_grpc_enabled:
        _workers.extend([
            # z4j-scheduler buffered-fire replay. Sweeps expired
            # buffers + replays fires whose project just got an
            # online agent for the right engine.
            PeriodicWorker(
                name="pending_fires_replay_worker",
                tick=PendingFiresReplayWorker(
                    db=db, dispatcher=command_dispatcher,
                ).tick,
                interval_seconds=float(
                    settings.pending_fires_replay_interval_seconds,
                ),
            ),
            # z4j-scheduler circuit breaker. Auto-disables a
            # schedule after N consecutive failed fires so a
            # broken schedule doesn't flood the dashboard.
            PeriodicWorker(
                name="schedule_circuit_breaker_worker",
                tick=ScheduleCircuitBreakerWorker(
                    db=db, settings=settings, audit=audit_service,
                ).tick,
                interval_seconds=float(
                    settings.schedule_circuit_breaker_interval_seconds,
                ),
            ),
            # z4j-scheduler fire-history retention. Bounds the
            # ``schedule_fires`` table at the operator-configured
            # window. Hourly is fine - the table doesn't need
            # tight retention.
            PeriodicWorker(
                name="schedule_fires_prune_worker",
                tick=ScheduleFiresPruneWorker(
                    db=db, settings=settings,
                ).tick,
                interval_seconds=3600.0,
            ),
        ])
    supervisor = WorkerSupervisor(workers=_workers)

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "z4j brain starting",
            version=__version__,
            environment=settings.environment,
        )
        try:
            await run_first_boot_check(
                db=db, setup_service=setup_service, settings=settings,
            )
        except Exception:  # noqa: BLE001
            # CRITICAL severity: a failure here usually means the
            # database is unreachable, which means the brain is
            # going to fail every request that hits the DB. We
            # still continue (so /api/v1/health stays up for the
            # liveness probe) but the operator MUST see this in
            # the logs without scrolling.
            logger.critical(
                "z4j brain first-boot check failed; brain will be unhealthy "
                "on any DB-touching request",
                exc_info=True,
            )

        # Schema version check: verify the database was not migrated
        # by a newer version of z4j-brain. If it was, refuse to start
        # to prevent data corruption.
        try:
            async with db.session() as version_session:
                await check_and_update_schema_version(version_session)
        except SchemaVersionError as exc:
            # Surface the full actionable message (what DB has, what
            # code has, what the operator should do). Previously this
            # logged "See logs above for details" which referenced
            # nothing the operator could actually see in stdout.
            logger.critical(
                "z4j brain REFUSING TO START: %s",
                str(exc),
            )
            raise
        except Exception:  # noqa: BLE001
            logger.warning(
                "z4j brain: schema version check failed (non-fatal)",
                exc_info=True,
            )

        # Shared HTTP client for notification channel dispatchers
        # (PERF-04). One pooled client per worker process - keep-alive
        # + connection pool across all outbound webhook / slack /
        # telegram deliveries instead of a fresh TCP+TLS handshake per
        # send.
        from z4j_brain.domain.notifications.channels import (
            set_shared_client as _set_notification_http_client,
        )

        notification_http_client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=100,
                max_connections=200,
            ),
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=False,
        )
        app.state.notification_http_client = notification_http_client
        _set_notification_http_client(notification_http_client)

        try:
            await registry.start()
        except Exception:  # noqa: BLE001
            logger.exception(
                "z4j brain registry.start crashed; continuing",
            )
        try:
            await dashboard_hub.start()
        except Exception:  # noqa: BLE001
            logger.exception(
                "z4j brain dashboard_hub.start crashed; continuing",
            )
        try:
            await supervisor.start()
        except Exception:  # noqa: BLE001
            logger.exception(
                "z4j brain worker supervisor start crashed; continuing",
            )

        if scheduler_grpc_server is not None:
            try:
                await scheduler_grpc_server.start()
            except Exception:  # noqa: BLE001
                # Hard failure here means TLS material is missing or
                # the bind port is taken. Brain still serves REST so
                # operators can investigate, but the scheduler will
                # be unable to connect. Log critical so the operator
                # sees it without scrolling.
                logger.critical(
                    "z4j brain scheduler_grpc start failed; scheduler "
                    "will be unable to connect",
                    exc_info=True,
                )

        try:
            yield
        finally:
            # Close the singleton TriggerScheduleClient (lazy-built
            # by the schedules trigger route). Failure here is non-
            # fatal - the channel is going away anyway.
            trig_client = getattr(
                app.state, "scheduler_trigger_client", None,
            )
            if trig_client is not None:
                try:
                    await trig_client.close()
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "z4j brain scheduler_trigger_client close crashed",
                    )
            if scheduler_grpc_server is not None:
                try:
                    await scheduler_grpc_server.stop()
                except Exception:  # noqa: BLE001
                    logger.exception("z4j brain scheduler_grpc stop crashed")
            try:
                await supervisor.stop()
            except Exception:  # noqa: BLE001
                logger.exception("z4j brain supervisor stop crashed")
            try:
                await dashboard_hub.stop()
            except Exception:  # noqa: BLE001
                logger.exception("z4j brain dashboard_hub stop crashed")
            try:
                await registry.stop()
            except Exception:  # noqa: BLE001
                logger.exception("z4j brain registry stop crashed")
            # Tear down the shared notification HTTP client AFTER the
            # workers have stopped so any in-flight delivery can drain.
            try:
                _set_notification_http_client(None)
                await notification_http_client.aclose()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "z4j brain notification_http_client close crashed",
                )
            await db.dispose()
            logger.info("z4j brain stopped")

    app = FastAPI(
        title="z4j brain",
        version=__version__,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
        lifespan=_lifespan,
    )

    # Bind every singleton onto app.state.
    app.state.settings = settings
    app.state.db = db
    app.state.password_hasher = hasher
    app.state.audit_service = audit_service
    app.state.auth_service = auth_service
    app.state.setup_service = setup_service
    app.state.event_ingestor = ingestor
    app.state.command_dispatcher = command_dispatcher
    app.state.brain_registry = registry
    app.state.dashboard_hub = dashboard_hub
    app.state.worker_supervisor = supervisor

    # ------------------------------------------------------------------
    # Middleware (outermost first)
    # ------------------------------------------------------------------
    app.add_middleware(ErrorMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(HostValidationMiddleware, settings=settings)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(RealClientIPMiddleware, resolver=proxy_resolver)
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.max_payload_size_bytes,
    )
    if settings.cors_origins:
        # Block dangerous wildcard + credentials combo (leaks session
        # cookies cross-origin). Log a warning and disable credentials.
        cors_creds = settings.cors_allow_credentials
        if "*" in settings.cors_origins and cors_creds:
            logger.warning(
                "z4j SECURITY: CORS wildcard '*' with allow_credentials=true "
                "is unsafe - credentials disabled. Use explicit origins.",
            )
            cors_creds = False
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=cors_creds,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
        )

    # ------------------------------------------------------------------
    # Routers
    # ------------------------------------------------------------------
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(setup.router_api, prefix="/api/v1")
    app.include_router(setup.router_html)  # /setup at the root

    app.include_router(projects_api.router, prefix="/api/v1")
    app.include_router(agents_api.router, prefix="/api/v1")
    app.include_router(api_keys_api.router, prefix="/api/v1")
    app.include_router(tasks_api.router, prefix="/api/v1")
    app.include_router(events_api.router, prefix="/api/v1")
    app.include_router(workers_api.router, prefix="/api/v1")
    app.include_router(queues_api.router, prefix="/api/v1")
    app.include_router(commands_api.router, prefix="/api/v1")
    app.include_router(schedules_api.router, prefix="/api/v1")
    app.include_router(audit_api.router, prefix="/api/v1")
    app.include_router(stats_api.router, prefix="/api/v1")
    app.include_router(trends_api.router, prefix="/api/v1")
    app.include_router(home_api.router, prefix="/api/v1")
    app.include_router(memberships_api.router, prefix="/api/v1")
    app.include_router(users_api.router, prefix="/api/v1")
    app.include_router(notifications_api.router, prefix="/api/v1")
    app.include_router(user_notifications_api.router, prefix="/api/v1")
    app.include_router(agent_longpoll_api.router, prefix="/api/v1")
    # Invitations - two routers (admin project-scoped + public accept).
    app.include_router(invitations_api.admin_router, prefix="/api/v1")
    app.include_router(invitations_api.public_router, prefix="/api/v1")

    # /metrics is mounted at the root for Prometheus scrapers.
    app.include_router(metrics_api.router)
    if settings.metrics_enabled:
        log = structlog.get_logger("z4j.brain")
        if settings.metrics_public:
            # Operator explicitly opted into unauthenticated /metrics
            # (Z4J_METRICS_PUBLIC=1). Log loudly so the choice is
            # visible in logs and doesn't drift into a forgotten default.
            log.warning(
                "metrics_public_opt_in",
                message=(
                    "/metrics is exposed WITHOUT authentication "
                    "(Z4J_METRICS_PUBLIC=1). Prometheus labels leak "
                    "project IDs, queue/task names, and in-memory state "
                    "to anyone who can reach the endpoint. Only safe on "
                    "a trusted closed network (localhost, sidecar "
                    "Prometheus, private LAN). Unset Z4J_METRICS_PUBLIC "
                    "and use Z4J_METRICS_AUTH_TOKEN for production."
                ),
            )
        elif settings.metrics_auth_token is None:
            # Fail-secure branch: no token AND no public opt-in.
            # /metrics will return 401 until the operator resolves this.
            # Normally unreachable on a CLI-launched brain (auto-mint
            # runs at boot), but custom bootstrappers may land here.
            log.warning(
                "metrics_no_auth_configured",
                message=(
                    "/metrics will return 401 - neither "
                    "Z4J_METRICS_AUTH_TOKEN nor Z4J_METRICS_PUBLIC is "
                    "set. Run `z4j metrics-token` to print an auto-minted "
                    "token, or set Z4J_METRICS_PUBLIC=1 for closed "
                    "networks."
                ),
            )
        else:
            log.info(
                "metrics_auth_enabled",
                message=(
                    "/metrics requires Authorization: Bearer <token>. "
                    "Run `z4j metrics-token` to print the token for "
                    "Prometheus scrape config."
                ),
            )

    # WebSocket gateway - mounted at the root, not under /api/v1.
    app.include_router(ws_gateway.router)
    app.include_router(ws_dashboard_gateway.router)

    # ------------------------------------------------------------------
    # RFC 9116 security.txt
    # ------------------------------------------------------------------
    # Served at both the canonical ``/.well-known/security.txt`` path
    # and (for older scanners) ``/security.txt``. Content is generated
    # once per boot from the current Settings + VERSION so the
    # expires date and contact URL reflect whatever the deployment is
    # actually configured with.
    from fastapi.responses import PlainTextResponse as _PlainText

    def _security_txt() -> _PlainText:
        # 1-year expiry is the RFC 9116 recommendation. The exact
        # date is re-computed at boot so a long-running instance
        # refreshes on restart - no stale signatures.
        from datetime import UTC, datetime, timedelta

        expires = (datetime.now(UTC) + timedelta(days=365)).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        )
        body = (
            "# z4j security disclosure policy\n"
            "# See https://github.com/z4jdev/z4j/blob/main/SECURITY.md\n"
            "\n"
            "Contact: mailto:security@z4j.com\n"
            f"Expires: {expires}\n"
            "Preferred-Languages: en\n"
            "Policy: https://github.com/z4jdev/z4j/blob/main/SECURITY.md\n"
            "Acknowledgments: https://github.com/z4jdev/z4j/security/advisories\n"
        )
        return _PlainText(body, media_type="text/plain; charset=utf-8")

    @app.get("/.well-known/security.txt", include_in_schema=False)
    async def security_txt_wellknown() -> _PlainText:  # noqa: D401
        return _security_txt()

    @app.get("/security.txt", include_in_schema=False)
    async def security_txt_legacy() -> _PlainText:  # noqa: D401
        return _security_txt()

    # ------------------------------------------------------------------
    # Dashboard SPA static mount (B6)
    # ------------------------------------------------------------------
    # The dashboard build is copied into ``settings.dashboard_dist`` by
    # the production Dockerfile. Mounted only when present so the test
    # suite (which never builds the dashboard) and the dev path
    # (Vite serves on its own port) both work without it.
    #
    # SPA fallback: TanStack Router owns every URL under the dashboard.
    # If an operator hard-refreshes ``/projects/default``, the browser
    # asks the brain for that exact path - there is no FastAPI route
    # for it, and a naive ``StaticFiles`` mount returns 404 because no
    # file exists at that path inside the dist. The fix is the
    # standard SPA-host pattern: mount the hashed asset directory
    # under ``/assets`` (so Vite's content-addressed bundles get
    # served directly with proper cache headers), and add a catch-all
    # route for everything else that:
    #
    #   1. tries to resolve the request to a real file inside the dist
    #      (favicon.svg, robots.txt, etc.) and serves it if found,
    #   2. otherwise falls back to ``index.html`` so the React router
    #      can take over client-side.
    #
    # The catch-all is registered LAST so it can't shadow ``/api/v1/*``,
    # ``/setup``, ``/metrics``, or the WebSocket routes - FastAPI
    # matches routes in declaration order.
    from pathlib import Path as _Path

    dashboard_dir = _Path(settings.dashboard_dist)
    # Fallback: check if the dashboard was bundled into the Python package
    # (pip install path where make dash-bundle copied dist/ into the wheel).
    if not dashboard_dir.is_dir():
        pkg_dashboard = _Path(__file__).resolve().parent / "dashboard" / "dist"
        if pkg_dashboard.is_dir():
            dashboard_dir = pkg_dashboard
    if dashboard_dir.is_dir() and not settings.disable_spa_fallback:
        from fastapi import HTTPException
        from fastapi.responses import FileResponse
        from starlette.staticfiles import StaticFiles

        # v1.0.19: SPA entry point must NOT be cached. Otherwise
        # browsers hold a stale ``index.html`` after a brain upgrade
        # that references hashed asset filenames which no longer
        # exist, and the dashboard appears blank until the user
        # hard-refreshes. Hashed assets under /assets/ keep their
        # default long-lived caching via StaticFiles, by design.
        _NO_CACHE_HEADERS = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }

        index_html = dashboard_dir / "index.html"
        assets_dir = dashboard_dir / "assets"
        if assets_dir.is_dir():
            # Vite emits hashed bundles into ``assets/`` - long-cache
            # them via StaticFiles so we don't reinvent ETag/Range
            # for the JS/CSS payload.
            app.mount(
                "/assets",
                StaticFiles(directory=assets_dir),
                name="dashboard-assets",
            )

        # Path prefixes that belong to the backend, never the SPA.
        # The catch-all explicitly 404s these so a typo'd
        # ``/api/v1/typoo`` returns clean JSON (or a 404 the
        # frontend can detect) instead of HTML masquerading as
        # an API response. v1.0.15 hardening: previously the
        # SPA fallback served ``index.html`` for any unmatched
        # path, which caused frontend code to choke on
        # ``Unexpected token '<'`` when an API URL was wrong.
        _BACKEND_PREFIXES = (
            "api/",
            "ws/",
            "metrics",
            "assets/",
            "auth/",
            "setup",
            "setup/",
            "healthz",
            "ready",
            "live",
            ".well-known/",
            "openapi.json",
            "docs",
            "redoc",
        )

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            """Serve a real file under the dist or fall back to index.html.

            Path traversal is blocked by resolving the candidate
            against the dist root and rejecting any escape via
            ``Path.relative_to``. Backend paths (``/api/``, ``/ws/``,
            ``/metrics``, etc.) always 404 from this fallback so a
            typo or a route registered after app build time gets a
            real 404 instead of HTML.
            """
            # Normalize: strip leading "/" if present (path:path
            # captures "api/v1/x" without the leading slash).
            normalized = full_path.lstrip("/")
            for prefix in _BACKEND_PREFIXES:
                if normalized == prefix.rstrip("/") or normalized.startswith(prefix):
                    raise HTTPException(status_code=404)
            if full_path:
                candidate = (dashboard_dir / full_path).resolve()
                try:
                    candidate.relative_to(dashboard_dir.resolve())
                except ValueError:
                    raise HTTPException(status_code=404) from None
                if candidate.is_file():
                    return FileResponse(candidate, headers=_NO_CACHE_HEADERS)
            return FileResponse(index_html, headers=_NO_CACHE_HEADERS)

    return app


__all__ = ["create_app"]
