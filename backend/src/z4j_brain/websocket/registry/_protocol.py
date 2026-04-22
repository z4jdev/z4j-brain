"""Registry Protocol shared by every implementation.

The protocol intentionally exposes a small surface so the two
implementations are easy to compare side-by-side.

Contract:

- ``register(agent_id, ws)`` - the gateway calls this once per
  successful handshake. The implementation MUST replace any
  existing entry for the same ``agent_id`` (the v1 policy is "one
  WebSocket per agent" - a second connection from the same
  agent kills the first one).
- ``unregister(agent_id)`` - called from the disconnect handler
  AND from the "second connection wins" path inside register.
- ``is_online(agent_id)`` - fast cluster-wide check used by the
  REST endpoints to render agent state. The Postgres impl
  consults the local map first AND optionally falls back to a
  cross-worker check; in v1 we accept that "is_online" reflects
  the union of "any worker that has touched the registry" - good
  enough for dashboard rendering.
- ``deliver(command_id, agent_id)`` - the load-bearing call. The
  caller has already INSERTed a ``commands`` row with status
  ``pending``; this asks the cluster to push it. Returns a
  :class:`DeliveryResult` describing what happened locally.
  ACTUAL delivery confirmation arrives later as a
  ``command_result`` frame on whichever worker holds the
  WebSocket - the timeout sweeper handles the case where it
  never arrives.

Implementations are free to be backend-specific below the line -
the Protocol exists so :mod:`z4j_brain.domain.command_dispatcher`
and the route layer never need to know.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from fastapi import WebSocket


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Outcome of a single ``deliver`` call.

    Attributes:
        delivered_locally: True if the agent was connected to THIS
            worker and the frame was pushed synchronously. The
            ``commands`` row has been UPDATEd to ``dispatched``.
        notified_cluster: True if a NOTIFY was published. The local
            map did not hold the agent, so some other worker may or
            may not pick it up. ``CommandTimeoutWorker`` is the
            authority on what actually happened.
        agent_was_known: True if the local map OR cluster reported
            the agent online. Lets the caller distinguish "we
            asked the cluster" from "no one in the cluster has
            this agent connected".
    """

    delivered_locally: bool
    notified_cluster: bool
    agent_was_known: bool


class BrainRegistry(Protocol):
    """Where commands go when they need to find their agent."""

    async def register(
        self,
        *,
        project_id: UUID,
        agent_id: UUID,
        ws: "WebSocket",
    ) -> None: ...

    async def unregister(self, agent_id: UUID) -> None: ...

    def is_online(self, agent_id: UUID) -> bool: ...

    async def deliver(
        self,
        *,
        command_id: UUID,
        agent_id: UUID,
    ) -> DeliveryResult: ...

    async def start(self) -> None:
        """Start any background tasks the implementation needs.

        Called once from the brain's lifespan startup.
        ``LocalRegistry`` is a no-op; ``PostgresNotifyRegistry``
        spawns the listener task and watchdog here.
        """

    async def stop(self) -> None:
        """Cleanly stop background tasks. Called from lifespan shutdown."""


__all__ = ["BrainRegistry", "DeliveryResult"]
