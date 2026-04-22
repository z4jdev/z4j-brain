"""In-process :class:`BrainRegistry` implementation.

Used by unit tests for speed and by single-worker development
loops where Postgres NOTIFY is unnecessary overhead. NEVER set
this as the production backend - it does not route across worker
processes, so commands issued from worker A targeting an agent on
worker B silently disappear.

The implementation is a thin wrapper around a single ``dict``
keyed by ``agent_id``. Concurrent register/unregister is safe via
an :class:`asyncio.Lock`; the dict itself is single-task-owned.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable, Awaitable
from uuid import UUID

import structlog

from z4j_brain.websocket.registry._protocol import DeliveryResult

if TYPE_CHECKING:
    from fastapi import WebSocket


logger = structlog.get_logger("z4j.brain.registry.local")


#: Type of the per-command "deliver this command to the WS" callback
#: that the gateway gives the registry. The registry calls it from
#: the worker that owns the WebSocket. Returns True on successful
#: push, False on push failure (which the registry treats as
#: "not delivered locally").
LocalDeliverCallback = Callable[[UUID, "WebSocket"], Awaitable[bool]]


class LocalRegistry:
    """Single-process registry for tests + single-worker dev mode."""

    def __init__(self, *, deliver_local: LocalDeliverCallback) -> None:
        self._lock = asyncio.Lock()
        self._connections: dict[UUID, "WebSocket"] = {}
        self._project_for_agent: dict[UUID, UUID] = {}
        self._deliver_local = deliver_local

    # ------------------------------------------------------------------
    # BrainRegistry
    # ------------------------------------------------------------------

    async def register(
        self,
        *,
        project_id: UUID,
        agent_id: UUID,
        ws: "WebSocket",
    ) -> None:
        async with self._lock:
            existing = self._connections.get(agent_id)
            if existing is not None and existing is not ws:
                # v1 policy: one WS per agent. The new connection
                # wins; the old one is force-closed.
                try:
                    await existing.close(code=4002)
                except Exception:  # noqa: BLE001
                    pass
            self._connections[agent_id] = ws
            self._project_for_agent[agent_id] = project_id

    async def unregister(self, agent_id: UUID) -> None:
        async with self._lock:
            self._connections.pop(agent_id, None)
            self._project_for_agent.pop(agent_id, None)

    def is_online(self, agent_id: UUID) -> bool:
        return agent_id in self._connections

    async def deliver(
        self,
        *,
        command_id: UUID,
        agent_id: UUID,
    ) -> DeliveryResult:
        ws = self._connections.get(agent_id)
        if ws is None:
            return DeliveryResult(
                delivered_locally=False,
                notified_cluster=False,
                agent_was_known=False,
            )
        try:
            ok = await self._deliver_local(command_id, ws)
        except Exception:  # noqa: BLE001
            logger.exception(
                "z4j local registry deliver crashed",
                command_id=str(command_id),
                agent_id=str(agent_id),
            )
            ok = False
        return DeliveryResult(
            delivered_locally=ok,
            notified_cluster=False,
            agent_was_known=True,
        )

    async def start(self) -> None:
        # No background tasks. The lifespan call still goes through
        # so the brain factory can treat the two registries
        # uniformly.
        return None

    async def stop(self) -> None:
        async with self._lock:
            for ws in list(self._connections.values()):
                try:
                    await ws.close(code=1001)
                except Exception:  # noqa: BLE001
                    pass
            self._connections.clear()
            self._project_for_agent.clear()


__all__ = ["LocalDeliverCallback", "LocalRegistry"]
