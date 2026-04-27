"""gRPC server lifecycle for the brain-side ``SchedulerService``.

Started by the brain's main lifespan when
``Z4J_SCHEDULER_GRPC_ENABLED`` is true. Bound to
``Z4J_SCHEDULER_GRPC_BIND_HOST`` :
``Z4J_SCHEDULER_GRPC_BIND_PORT`` (default ``0.0.0.0:7701``).

The server runs alongside the FastAPI process. It uses ``grpc.aio``
so it cooperates with FastAPI's asyncio loop without spawning a
threadpool. On shutdown, in-flight RPCs get a
``Z4J_SCHEDULER_GRPC_GRACE_SECONDS`` window to drain before the
runtime is torn down.

Wire-in pattern (called from ``z4j_brain.main._lifespan``):

    server = SchedulerGrpcServer(
        settings=settings,
        db=db,
        command_dispatcher=command_dispatcher,
        audit_service=audit_service,
    )
    await server.start()
    try:
        yield
    finally:
        await server.stop()

A disabled-via-settings server returns immediately on every
lifecycle method so callers don't need a separate gating branch.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import grpc

from z4j_brain.scheduler_grpc.auth import SchedulerAllowlistInterceptor
from z4j_brain.scheduler_grpc.handlers import SchedulerServiceImpl
from z4j_brain.scheduler_grpc.proto import scheduler_pb2_grpc as pb_grpc

if TYPE_CHECKING:  # pragma: no cover
    from z4j_brain.domain.audit_service import AuditService
    from z4j_brain.domain.command_dispatcher import CommandDispatcher
    from z4j_brain.persistence.database import DatabaseManager
    from z4j_brain.settings import Settings

logger = logging.getLogger("z4j.brain.scheduler_grpc.server")


class SchedulerGrpcServer:
    """Owns the lifecycle of the brain-side ``SchedulerService`` server.

    Construction is cheap (no I/O); the gRPC runtime is created on
    :meth:`start`. :meth:`stop` is idempotent and may be called even
    if :meth:`start` was never called or returned without binding
    (``Z4J_SCHEDULER_GRPC_ENABLED=false``).
    """

    def __init__(
        self,
        *,
        settings: Settings,
        db: DatabaseManager,
        command_dispatcher: CommandDispatcher,
        audit_service: AuditService,
    ) -> None:
        self._settings = settings
        self._db = db
        self._dispatcher = command_dispatcher
        self._audit = audit_service
        self._server: grpc.aio.Server | None = None
        # Captured from ``add_secure_port`` so callers binding to an
        # ephemeral port (e.g. integration tests passing port=0) can
        # discover the actual port the OS assigned.
        self._bound_port: int = 0

    @property
    def bound_port(self) -> int:
        """The port the gRPC server is currently listening on.

        Returns 0 before :meth:`start` has been called or when the
        server is disabled. After :meth:`start` returns, this is
        the actual TCP port (matters when ``bind_port=0`` was used
        to request an ephemeral port from the OS).
        """
        return self._bound_port

    async def start(self) -> None:
        """Bind the gRPC port and start serving.

        Returns immediately if ``Z4J_SCHEDULER_GRPC_ENABLED`` is
        false (operator hasn't opted in). Otherwise raises if the
        TLS material is missing/invalid - we fail loud rather than
        silently fall back to insecure mode, because the scheduler
        wire is the operator's most-privileged channel into the
        brain.
        """
        if not self._settings.scheduler_grpc_enabled:
            logger.info(
                "z4j.brain.scheduler_grpc: disabled via settings; "
                "not starting server",
            )
            return

        creds = _build_server_credentials(self._settings)
        interceptors = (
            SchedulerAllowlistInterceptor(
                allowed_cns=tuple(self._settings.scheduler_grpc_allowed_cns),
            ),
        )
        server = grpc.aio.server(interceptors=interceptors)
        servicer = SchedulerServiceImpl(
            settings=self._settings,
            db=self._db,
            command_dispatcher=self._dispatcher,
            audit_service=self._audit,
        )
        pb_grpc.add_SchedulerServiceServicer_to_server(servicer, server)

        bind_addr = (
            f"{self._settings.scheduler_grpc_bind_host}"
            f":{self._settings.scheduler_grpc_bind_port}"
        )
        # ``add_secure_port`` returns the port that was actually bound;
        # capture it so test fixtures using port=0 can discover the
        # ephemeral port the kernel assigned.
        self._bound_port = server.add_secure_port(bind_addr, creds)
        await server.start()
        self._server = server
        logger.info(
            "z4j.brain.scheduler_grpc: serving on %s (mTLS, allow-list=%s)",
            bind_addr,
            tuple(self._settings.scheduler_grpc_allowed_cns) or "(open CA)",
        )

    async def stop(self) -> None:
        """Stop the gRPC server and drain in-flight RPCs.

        Idempotent: safe to call multiple times or before
        :meth:`start`.
        """
        if self._server is None:
            return
        grace = float(self._settings.scheduler_grpc_grace_seconds)
        try:
            await self._server.stop(grace=grace)
        except Exception:  # noqa: BLE001
            logger.exception(
                "z4j.brain.scheduler_grpc: server.stop crashed",
            )
        self._server = None
        logger.info("z4j.brain.scheduler_grpc: stopped")


def _build_server_credentials(settings: Settings) -> grpc.ServerCredentials:
    """Read the TLS material referenced by Settings into gRPC credentials.

    Three required env vars (per ``docs/SCHEDULER.md §22``):

    - ``Z4J_SCHEDULER_GRPC_TLS_CERT`` - server cert PEM
    - ``Z4J_SCHEDULER_GRPC_TLS_KEY`` - server key PEM
    - ``Z4J_SCHEDULER_GRPC_TLS_CA`` - CA bundle to validate clients

    Missing or unreadable material raises a ``RuntimeError`` with a
    pointer to the env var that's wrong; callers (the lifespan)
    surface this to the operator.
    """
    cert = _read_required_pem(
        settings.scheduler_grpc_tls_cert,
        "Z4J_SCHEDULER_GRPC_TLS_CERT",
    )
    key = _read_required_pem(
        settings.scheduler_grpc_tls_key,
        "Z4J_SCHEDULER_GRPC_TLS_KEY",
    )
    ca = _read_required_pem(
        settings.scheduler_grpc_tls_ca,
        "Z4J_SCHEDULER_GRPC_TLS_CA",
    )
    return grpc.ssl_server_credentials(
        private_key_certificate_chain_pairs=[(key, cert)],
        root_certificates=ca,
        # ``True`` = require client certs. We're explicitly running
        # mTLS, not optional client certs.
        require_client_auth=True,
    )


def _read_required_pem(path_str: str | None, env_var: str) -> bytes:
    """Read a PEM file, raising a clear error if missing/empty."""
    if not path_str:
        raise RuntimeError(
            f"scheduler_grpc enabled but {env_var} is not set",
        )
    path = Path(path_str)
    if not path.is_file():
        raise RuntimeError(
            f"{env_var} points at {path_str!r} which does not exist",
        )
    data = path.read_bytes()
    if not data.strip():
        raise RuntimeError(f"{env_var} file at {path_str!r} is empty")
    return data


__all__ = ["SchedulerGrpcServer"]
