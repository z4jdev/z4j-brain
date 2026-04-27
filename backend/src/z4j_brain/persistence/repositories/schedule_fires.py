"""``schedule_fires`` repository.

Three operation surfaces:

- :meth:`record` - the FireSchedule handler creates a row when
  it dispatches (status=delivered/buffered/failed).
- :meth:`acknowledge` - the AcknowledgeFireResult handler updates
  the row with ack outcome + latency.
- :meth:`list_recent_for_schedule` / :meth:`recent_failures` -
  read paths for the dashboard + circuit breaker worker.

Inserts are idempotent on ``fire_id`` so a scheduler retry doesn't
duplicate the row. Updates are bounded WHERE-clauses so the
acknowledge path can't accidentally rewrite history.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from z4j_brain.persistence.models import ScheduleFire


class ScheduleFireRepository:
    """``schedule_fires`` table CRUD."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        fire_id: UUID,
        schedule_id: UUID,
        project_id: UUID,
        command_id: UUID | None,
        status: str,
        scheduled_for: datetime,
        fired_at: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ScheduleFire:
        """Insert one fire row. Idempotent on ``fire_id``.

        A second insert of the same fire_id returns the existing
        row instead of raising. Lets the FireSchedule retry path
        write the row blindly without TOCTOU concerns.
        """
        row = ScheduleFire(
            fire_id=fire_id,
            schedule_id=schedule_id,
            project_id=project_id,
            command_id=command_id,
            status=status,
            scheduled_for=scheduled_for,
            fired_at=fired_at or datetime.now(UTC),
            error_code=error_code,
            error_message=(
                error_message[:2000] if error_message else None
            ),
        )
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            existing = await self._get_by_fire_id(fire_id)
            if existing is None:
                raise
            return existing
        return row

    async def acknowledge(
        self,
        *,
        fire_id: UUID,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ScheduleFire | None:
        """Update an existing row with the ack outcome + latency.

        Returns the updated row, or ``None`` if no row exists for
        this ``fire_id`` (the ack arrived before the dispatch row
        was committed - rare but possible under tight timing).

        ``latency_ms`` is computed here from
        ``acked_at - fired_at`` so callers don't have to.
        """
        row = await self._get_by_fire_id(fire_id)
        if row is None:
            return None
        now = datetime.now(UTC)
        row.acked_at = now
        row.status = status
        if error_code is not None:
            row.error_code = error_code[:64]
        if error_message is not None:
            row.error_message = error_message[:2000]
        # Compute latency. SQLite drops tz info on stored timestamps
        # so ``row.fired_at`` may come back naive even though we
        # wrote it tz-aware. Normalise both sides to naive UTC for
        # the subtraction. Postgres preserves tz; the
        # replace(tzinfo=None) is a no-op on already-naive values.
        if row.fired_at is not None:
            now_naive = now.replace(tzinfo=None)
            fired_at_naive = (
                row.fired_at.replace(tzinfo=None)
                if row.fired_at.tzinfo is not None
                else row.fired_at
            )
            delta = (now_naive - fired_at_naive).total_seconds() * 1000
            row.latency_ms = int(max(0, delta))
        await self.session.flush()
        return row

    async def list_recent_for_schedule(
        self,
        *,
        schedule_id: UUID,
        project_id: UUID,
        limit: int = 100,
    ) -> list[ScheduleFire]:
        """Newest-first fire history for one schedule.

        Project-scoped to defend against IDOR via guessed
        schedule_ids. Returns at most ``limit`` rows.
        """
        result = await self.session.execute(
            select(ScheduleFire)
            .where(
                ScheduleFire.schedule_id == schedule_id,
                ScheduleFire.project_id == project_id,
            )
            .order_by(ScheduleFire.fired_at.desc())
            .limit(limit),
        )
        return list(result.scalars().all())

    async def recent_failures(
        self,
        *,
        schedule_id: UUID,
        limit: int,
    ) -> list[ScheduleFire]:
        """Last N fires for circuit-breaker evaluation.

        Returns the LAST N rows regardless of status. The caller
        decides whether the streak is consecutive-failed (i.e.
        every row in the slice has ``status in ('failed',
        'acked_failed')``). Fetching all-status rows lets the
        worker distinguish "10 failures in a row" from "5 failures
        and 5 successes interleaved" - the second is healthy.
        """
        result = await self.session.execute(
            select(ScheduleFire)
            .where(ScheduleFire.schedule_id == schedule_id)
            .order_by(ScheduleFire.fired_at.desc())
            .limit(limit),
        )
        return list(result.scalars().all())

    async def delete_older_than(
        self, *, cutoff: datetime,
    ) -> int:
        """Sweep rows older than ``cutoff``. Returns rows deleted.

        Called by the periodic retention worker. The 30-day default
        keeps the table bounded at typical fire rates (10 schedules
        × 1 fire/min × 30d ≈ 430k rows).
        """
        result = await self.session.execute(
            delete(ScheduleFire).where(ScheduleFire.fired_at < cutoff),
        )
        return result.rowcount or 0

    async def _get_by_fire_id(self, fire_id: UUID) -> ScheduleFire | None:
        result = await self.session.execute(
            select(ScheduleFire).where(ScheduleFire.fire_id == fire_id),
        )
        return result.scalar_one_or_none()


__all__ = ["ScheduleFireRepository"]
