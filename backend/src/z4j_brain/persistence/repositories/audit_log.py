"""``audit_log`` repository.

Insert-only by application convention; the database trigger on
Postgres also enforces it. Concrete callers go through
:class:`AuditService`, NOT this repository directly - the service
owns the row HMAC and the canonicalisation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from z4j_brain.persistence.models import AuditLog
from z4j_brain.persistence.repositories._base import BaseRepository


class AuditLogRepository(BaseRepository[AuditLog]):
    """Append-only access to the audit log."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AuditLog)

    async def insert(
        self,
        *,
        id: UUID | None = None,
        action: str,
        target_type: str,
        target_id: str | None,
        result: str,
        outcome: str | None,
        event_id: UUID | None,
        user_id: UUID | None,
        project_id: UUID | None,
        source_ip: str | None,
        user_agent: str | None,
        metadata: dict[str, Any],
        row_hmac: str,
        occurred_at: datetime,
        prev_row_hmac: str | None = None,
    ) -> AuditLog:
        """Insert one row with the AuditService-supplied row HMAC.

        ``prev_row_hmac`` links this row into the HMAC chain
        (audit v3 - finding A8). AuditService fetches the prior
        row's hmac via ``get_latest_row_hmac`` before building
        this row's input so deleting any chained row breaks the
        next chained row's anchor - detectable by
        ``AuditService.verify_chain``.
        """
        kwargs: dict[str, Any] = dict(
            action=action,
            target_type=target_type,
            target_id=target_id,
            result=result,
            outcome=outcome,
            event_id=event_id,
            user_id=user_id,
            project_id=project_id,
            source_ip=source_ip,
            user_agent=user_agent,
            audit_metadata=metadata,
            row_hmac=row_hmac,
            prev_row_hmac=prev_row_hmac,
            occurred_at=occurred_at,
        )
        if id is not None:
            kwargs["id"] = id
        row = AuditLog(**kwargs)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_latest_row_hmac(self) -> str | None:
        """Return the row_hmac of the most recently inserted row.

        Used by ``AuditService.record`` to build the HMAC chain
        anchor (audit v3 - finding A8). Returns None for the very
        first row ever written (genesis). ORDER BY id DESC relies
        on UUIDv7 time-ordering on Postgres 18+; the SQLite dev
        path uses uuid4 so we ORDER BY occurred_at instead.
        """
        stmt = (
            select(AuditLog.row_hmac)
            .order_by(AuditLog.occurred_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_recent_by_action_and_ip(
        self,
        *,
        action_prefix: str,
        source_ip: str,
        since: datetime,
    ) -> int:
        """Return the number of audit rows matching prefix + ip + window.

        Used by the SetupService to enforce a per-IP rate limit on
        ``setup.attempt`` rows that survives across worker restarts
        and across multiple uvicorn workers - a per-process deque
        cannot do that. The query is bounded by the
        ``ix_audit_log_action_pattern`` index added in migration
        0002 (``(project_id, action text_pattern_ops, occurred_at DESC)``)
        for fast prefix lookups.
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action.like(f"{action_prefix}%"),
                AuditLog.source_ip == source_ip,
                AuditLog.occurred_at >= since,
            ),
        )
        return int(result.scalar_one() or 0)

    async def stream_for_verify(
        self,
        *,
        chunk: int = 500,
    ) -> list[AuditLog]:
        """Return rows for offline HMAC verification.

        v1 returns up to ``chunk`` rows starting from the oldest;
        the verifier CLI re-invokes with ``offset`` for paging.
        Hot-table-aware: we never load the whole table into memory.
        """
        if chunk <= 0 or chunk > 5000:
            raise ValueError("chunk must be between 1 and 5000")
        result = await self.session.execute(
            select(AuditLog)
            .order_by(AuditLog.occurred_at.asc(), AuditLog.id.asc())
            .limit(chunk),
        )
        return list(result.scalars().all())


__all__ = ["AuditLogRepository"]
