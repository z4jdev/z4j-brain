"""``agents`` repository."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from z4j_brain.persistence.enums import AgentState
from z4j_brain.persistence.models import Agent
from z4j_brain.persistence.repositories._base import BaseRepository


class AgentRepository(BaseRepository[Agent]):
    """Agent CRUD + heartbeat / state bookkeeping."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Agent)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    async def get_by_token_hash(self, token_hash: str) -> Agent | None:
        """Resolve an agent by its bearer-token HMAC hash.

        Used by :mod:`z4j_brain.websocket.auth` to authenticate
        inbound WebSocket handshakes. Single PK-equivalent index
        lookup (``token_hash`` is UNIQUE).
        """
        result = await self.session.execute(
            select(Agent).where(Agent.token_hash == token_hash),
        )
        return result.scalar_one_or_none()

    async def list_for_project(self, project_id: UUID) -> list[Agent]:
        """Return every agent registered to one project."""
        result = await self.session.execute(
            select(Agent)
            .where(Agent.project_id == project_id)
            .order_by(Agent.created_at.desc()),
        )
        return list(result.scalars().all())

    async def list_online_for_project(
        self, project_id: UUID,
    ) -> list[Agent]:
        """Return only the currently-online agents for a project.

        Used by the ReconciliationWorker to pick an agent to dispatch
        a reconcile probe to. Ordered by most-recently-seen first so
        the freshest WS slot gets picked.
        """
        from z4j_brain.persistence.enums import AgentState

        result = await self.session.execute(
            select(Agent)
            .where(
                Agent.project_id == project_id,
                Agent.state == AgentState.ONLINE,
            )
            .order_by(Agent.last_seen_at.desc()),
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # State updates - used by the gateway + AgentHealthWorker
    # ------------------------------------------------------------------

    async def mark_online(
        self,
        agent_id: UUID,
        *,
        protocol_version: str,
        framework_adapter: str,
        engine_adapters: list[str],
        scheduler_adapters: list[str],
        capabilities: dict[str, Any],
        host: dict[str, Any] | None = None,
    ) -> None:
        """Set state=online + bump connect/seen + refresh handshake metadata.

        ``host`` carries the agent's optional ``host`` dict from the hello
        frame's payload (currently the operator-provided ``host.name`` label).
        Stored under ``agent_metadata['host']`` so it survives across the
        existing schema without a migration. Dashboards surface
        ``host.name`` next to the mint-time agent name.
        """
        now = datetime.now(UTC)
        # Read-modify-write the JSONB metadata column so we don't trample
        # other keys a future feature might add. Single round-trip via a
        # SELECT-then-UPDATE is fine here - mark_online runs once per
        # agent connection, not per frame.
        if host:
            row = await self.session.execute(
                select(Agent.agent_metadata).where(Agent.id == agent_id),
            )
            current_meta = row.scalar_one_or_none() or {}
            new_meta = dict(current_meta)
            new_meta["host"] = dict(host)
            await self.session.execute(
                update(Agent)
                .where(Agent.id == agent_id)
                .values(
                    state=AgentState.ONLINE,
                    last_connect_at=now,
                    last_seen_at=now,
                    protocol_version=protocol_version,
                    framework_adapter=framework_adapter,
                    engine_adapters=engine_adapters,
                    scheduler_adapters=scheduler_adapters,
                    capabilities=capabilities,
                    agent_metadata=new_meta,
                ),
            )
        else:
            await self.session.execute(
                update(Agent)
                .where(Agent.id == agent_id)
                .values(
                    state=AgentState.ONLINE,
                    last_connect_at=now,
                    last_seen_at=now,
                    protocol_version=protocol_version,
                    framework_adapter=framework_adapter,
                    engine_adapters=engine_adapters,
                    scheduler_adapters=scheduler_adapters,
                    capabilities=capabilities,
                ),
            )

    async def mark_offline(self, agent_id: UUID) -> None:
        """Set state=offline. Idempotent."""
        await self.session.execute(
            update(Agent)
            .where(Agent.id == agent_id)
            .values(state=AgentState.OFFLINE),
        )

    async def touch_heartbeat(self, agent_id: UUID) -> None:
        """Bump ``last_seen_at`` to now. Single indexed UPDATE."""
        await self.session.execute(
            update(Agent)
            .where(Agent.id == agent_id)
            .values(last_seen_at=datetime.now(UTC)),
        )

    async def sweep_offline(self, *, cutoff: datetime) -> int:
        """Mark every agent whose ``last_seen_at`` is older than ``cutoff``.

        Used by :class:`AgentHealthWorker`. Returns the number of
        rows transitioned. Single bulk UPDATE - no row scan.
        """
        result = await self.session.execute(
            update(Agent)
            .where(
                Agent.state == AgentState.ONLINE,
                Agent.last_seen_at < cutoff,
            )
            .values(state=AgentState.OFFLINE),
        )
        return int(result.rowcount or 0)

    async def prune_stale(self, *, cutoff: datetime) -> int:
        """Delete agents that have been offline past ``cutoff``.

        "Stale" means: ``state != online`` AND (``last_seen_at`` is
        older than cutoff OR the row has never connected at all
        AND ``created_at`` is older than cutoff). Invoked by the
        daily :class:`AgentHygieneWorker`; protects the Agents
        page from accumulating ghost rows after a container is
        removed without revoking the token first.

        Cascades via FK (``events.agent_id``, ``commands.agent_id``)
        as configured on the model - events lose their agent link
        but survive with ``agent_id=NULL``.
        """
        from sqlalchemy import delete, or_

        result = await self.session.execute(
            delete(Agent).where(
                Agent.state != AgentState.ONLINE,
                or_(
                    Agent.last_seen_at < cutoff,
                    # Never-connected agents (minted token then
                    # someone deleted the container before it
                    # booted) have last_seen_at=NULL; fall back to
                    # created_at so we can still prune them.
                    (Agent.last_seen_at.is_(None)) & (Agent.created_at < cutoff),
                ),
            ),
        )
        return int(result.rowcount or 0)

    async def insert(
        self,
        *,
        project_id: UUID,
        name: str,
        token_hash: str,
    ) -> Agent:
        """Create a new agent row with placeholder handshake fields.

        Used by the "mint agent token" admin endpoint. The handshake
        metadata (``protocol_version``, ``framework_adapter``,
        ``capabilities``) is overwritten when the agent first
        connects via :meth:`mark_online`.
        """
        agent = Agent(
            project_id=project_id,
            name=name,
            token_hash=token_hash,
            protocol_version="0",
            framework_adapter="unknown",
            engine_adapters=[],
            scheduler_adapters=[],
            capabilities={},
            state=AgentState.UNKNOWN,
        )
        self.session.add(agent)
        await self.session.flush()
        return agent


__all__ = ["AgentRepository"]
