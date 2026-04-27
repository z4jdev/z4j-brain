"""Brain-side gRPC client for the scheduler's TriggerSchedule RPC.

Sits opposite :class:`z4j_scheduler.trigger_grpc.server.TriggerGrpcServer`
- the dashboard's "fire now" button on a z4j-scheduler-managed
schedule routes through this client so the scheduler's local cache
gets the update (preventing the next tick from double-firing).

Connection model
----------------

One persistent ``grpc.aio.Channel`` per brain process. Construction
is cheap; :meth:`connect` opens the channel; :meth:`close` is
idempotent. Concurrent requests share the channel.

Operator deployment
-------------------

Brain needs three new env vars when ``Z4J_SCHEDULER_TRIGGER_URL``
is set:

- ``Z4J_SCHEDULER_TRIGGER_URL`` - host:port of the scheduler's
  trigger server, typically ``scheduler:7802``
- ``Z4J_SCHEDULER_TRIGGER_TLS_CERT`` - brain's client cert
- ``Z4J_SCHEDULER_TRIGGER_TLS_KEY`` - brain's client key
- ``Z4J_SCHEDULER_TRIGGER_TLS_CA`` - CA bundle for the scheduler's
  server cert

Without ``Z4J_SCHEDULER_TRIGGER_URL`` the brain falls back to its
existing direct-dispatch path on trigger-now (see ``api/schedules.py``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import grpc

from z4j_brain.scheduler_grpc.proto import scheduler_pb2 as pb
from z4j_brain.scheduler_grpc.proto import scheduler_pb2_grpc as pb_grpc

if TYPE_CHECKING:  # pragma: no cover
    from z4j_brain.settings import Settings

logger = logging.getLogger("z4j.brain.scheduler_grpc.trigger_client")

#: Per-call deadline for the unary TriggerSchedule RPC. Operators
#: clicking the dashboard expect feedback in seconds; we don't want
#: a hung scheduler to leave the request thread stuck for minutes.
_TRIGGER_TIMEOUT_SECONDS = 10.0


class TriggerScheduleClient:
    """Async gRPC client for the scheduler's TriggerSchedule RPC.

    Constructed lazily by the FastAPI dependency the trigger route
    uses; one instance per brain process.
    """

    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._channel: grpc.aio.Channel | None = None
        self._stub: pb_grpc.SchedulerServiceStub | None = None

    async def connect(self) -> None:
        """Open the channel + construct the stub. Idempotent."""
        if self._channel is not None:
            return
        if not self._settings.scheduler_trigger_url:
            raise RuntimeError(
                "TriggerScheduleClient.connect requires "
                "Z4J_SCHEDULER_TRIGGER_URL to be set",
            )
        creds = _build_client_credentials(self._settings)
        self._channel = grpc.aio.secure_channel(
            self._settings.scheduler_trigger_url,
            creds,
        )
        self._stub = pb_grpc.SchedulerServiceStub(self._channel)
        logger.info(
            "z4j.brain.scheduler_grpc.trigger_client: connected to %s",
            self._settings.scheduler_trigger_url,
        )

    async def close(self) -> None:
        """Close the channel cleanly. Idempotent."""
        if self._channel is None:
            return
        try:
            await self._channel.close(grace=2.0)
        finally:
            self._channel = None
            self._stub = None

    async def trigger(
        self,
        *,
        schedule_id: UUID,
        user_id: UUID | None,
        idempotency_key: str | None = None,
    ) -> pb.TriggerScheduleResponse:
        """Issue a TriggerSchedule call. Caller handles error_code."""
        if self._stub is None:
            await self.connect()
        assert self._stub is not None
        request = pb.TriggerScheduleRequest(
            schedule_id=str(schedule_id),
            user_id=str(user_id) if user_id is not None else "",
            idempotency_key=idempotency_key or "",
        )
        return await self._stub.TriggerSchedule(
            request, timeout=_TRIGGER_TIMEOUT_SECONDS,
        )


def _build_client_credentials(settings: Settings) -> grpc.ChannelCredentials:
    """Read TLS material from settings into ChannelCredentials."""
    cert = _read_required_pem(
        settings.scheduler_trigger_tls_cert,
        "Z4J_SCHEDULER_TRIGGER_TLS_CERT",
    )
    key = _read_required_pem(
        settings.scheduler_trigger_tls_key,
        "Z4J_SCHEDULER_TRIGGER_TLS_KEY",
    )
    ca = _read_required_pem(
        settings.scheduler_trigger_tls_ca,
        "Z4J_SCHEDULER_TRIGGER_TLS_CA",
    )
    return grpc.ssl_channel_credentials(
        root_certificates=ca,
        private_key=key,
        certificate_chain=cert,
    )


def _read_required_pem(path_str: str | None, env_var: str) -> bytes:
    if not path_str:
        raise RuntimeError(
            f"TriggerScheduleClient requires {env_var} to be set",
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


__all__ = ["TriggerScheduleClient"]
