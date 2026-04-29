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


#: Sentinel key for legacy 1.1.x agents that don't send a worker_id.
#: One legacy slot per agent_id, alongside any 1.2.0+ worker slots.
_LEGACY_SLOT = "__legacy__"


class LocalRegistry:
    """Single-process registry for tests + single-worker dev mode.

    1.2.0+: tracks multiple WebSockets per agent_id, keyed by
    worker_id. Legacy 1.1.x clients (worker_id=None) live under
    a sentinel ``__legacy__`` slot so the data shape stays
    homogeneous.
    """

    def __init__(self, *, deliver_local: LocalDeliverCallback) -> None:
        self._lock = asyncio.Lock()
        # agent_id -> {worker_id_or_legacy_sentinel: WebSocket}
        self._connections: dict[UUID, dict[str, "WebSocket"]] = {}
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
        worker_id: str | None = None,
    ) -> None:
        slot = worker_id if worker_id is not None else _LEGACY_SLOT
        async with self._lock:
            workers = self._connections.setdefault(agent_id, {})
            existing = workers.get(slot)
            if existing is not None and existing is not ws:
                # Same (agent_id, worker_id) reconnecting (or a
                # legacy-mode duplicate). Kick the old.
                try:
                    await existing.close(code=4002)
                except Exception:  # noqa: BLE001
                    pass
            workers[slot] = ws
            self._project_for_agent[agent_id] = project_id

    async def unregister(
        self,
        agent_id: UUID,
        *,
        ws: "WebSocket | None" = None,
        worker_id: str | None = None,
    ) -> None:
        """Drop one slot for ``agent_id`` from the registry.

        Round-7 audit fix R7-HIGH (race) (Apr 2026): callers should
        pass the ``ws`` they're tearing down so we only remove the
        registry entry IF that exact WebSocket is still the one
        registered. v1.2.0: also pass ``worker_id`` so we drop the
        right slot when an agent has multiple worker connections.

        ``ws=None`` keeps the legacy "drop unconditionally" behaviour
        for callers that don't track the ws (e.g. shutdown).
        """
        slot = worker_id if worker_id is not None else _LEGACY_SLOT
        async with self._lock:
            workers = self._connections.get(agent_id)
            if workers is None:
                return
            if ws is not None:
                current = workers.get(slot)
                if current is not ws:
                    # The new connection has already replaced this one;
                    # leave the registry entry intact.
                    return
            workers.pop(slot, None)
            if not workers:
                # Last worker for this agent disconnected.
                self._connections.pop(agent_id, None)
                self._project_for_agent.pop(agent_id, None)

    def is_online(self, agent_id: UUID) -> bool:
        workers = self._connections.get(agent_id)
        return bool(workers)

    async def deliver(
        self,
        *,
        command_id: UUID,
        agent_id: UUID,
    ) -> DeliveryResult:
        workers = self._connections.get(agent_id)
        if not workers:
            return DeliveryResult(
                delivered_locally=False,
                notified_cluster=False,
                agent_was_known=False,
            )
        # Pick any worker - first-available semantics. Future:
        # per-role routing (deliver schedule.fire to role=task,
        # config-update to role=web, etc.). For 1.2.0 we stay
        # role-agnostic; commands flow to whichever worker the
        # registry iteration yields first.
        ws = next(iter(workers.values()))
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
            for workers in list(self._connections.values()):
                for ws in list(workers.values()):
                    try:
                        await ws.close(code=1001)
                    except Exception:  # noqa: BLE001
                        pass
            self._connections.clear()
            self._project_for_agent.clear()


__all__ = ["LocalDeliverCallback", "LocalRegistry"]
