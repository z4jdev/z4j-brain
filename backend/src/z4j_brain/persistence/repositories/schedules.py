"""``schedules`` repository."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from z4j_brain.persistence.models import Schedule
from z4j_brain.persistence.repositories._base import BaseRepository


class ScheduleRepository(BaseRepository[Schedule]):
    """Schedule CRUD."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Schedule)

    async def list_for_project(self, project_id: UUID) -> list[Schedule]:
        result = await self.session.execute(
            select(Schedule)
            .where(Schedule.project_id == project_id)
            .order_by(Schedule.name),
        )
        return list(result.scalars().all())

    async def get_for_project(
        self,
        *,
        project_id: UUID,
        schedule_id: UUID,
    ) -> Schedule | None:
        """Resolve a schedule by id, scoped to a project.

        Returns ``None`` if the schedule exists but belongs to a
        different project - defends against IDOR via guessed UUIDs.
        """
        schedule = await self.get(schedule_id)
        if schedule is None or schedule.project_id != project_id:
            return None
        return schedule

    async def set_enabled(
        self,
        *,
        schedule_id: UUID,
        enabled: bool,
    ) -> bool:
        """Toggle the enabled flag. Returns True if a row was updated."""
        result = await self.session.execute(
            update(Schedule)
            .where(Schedule.id == schedule_id)
            .values(is_enabled=enabled, updated_at=datetime.now(UTC)),
        )
        return (result.rowcount or 0) > 0


    async def upsert_from_event(
        self,
        *,
        project_id: UUID,
        data: dict[str, Any],
    ) -> Schedule:
        """Upsert a schedule from an agent-side schedule event.

        The event payload carries the full schedule data including
        a name which is unique per project. We use
        ``(project_id, name)`` as the upsert key.
        """
        name = str(data.get("name", ""))
        if not name:
            raise ValueError("schedule event missing name")

        result = await self.session.execute(
            select(Schedule).where(
                Schedule.project_id == project_id,
                Schedule.name == name,
            ),
        )
        existing = result.scalar_one_or_none()
        now = datetime.now(UTC)

        from z4j_brain.persistence.enums import ScheduleKind

        kind_raw = data.get("kind", "interval")
        try:
            kind = ScheduleKind(kind_raw)
        except ValueError:
            kind = ScheduleKind.INTERVAL

        values = {
            "engine": data.get("engine", "celery"),
            "scheduler": data.get("scheduler", "celery-beat"),
            "task_name": data.get("task_name", "unknown"),
            "kind": kind,
            "expression": data.get("expression", ""),
            "timezone": data.get("timezone", "UTC"),
            "queue": data.get("queue"),
            "args": data.get("args", []),
            "kwargs": data.get("kwargs", {}),
            "is_enabled": data.get("is_enabled", True),
            "last_run_at": _parse_dt(data.get("last_run_at")),
            "next_run_at": _parse_dt(data.get("next_run_at")),
            "total_runs": data.get("total_runs", 0),
        }

        if existing is None:
            row = Schedule(
                project_id=project_id,
                name=name,
                **values,
            )
            self.session.add(row)
            await self.session.flush()
            return row
        for key, value in values.items():
            setattr(existing, key, value)
        existing.updated_at = now
        await self.session.flush()
        return existing


def _parse_dt(value: Any) -> datetime | None:
    """Best-effort ISO-8601 parse, returns None on failure."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Import support (z4j-scheduler migration importers)
# ---------------------------------------------------------------------------

#: Outcome bucket for one row in a bulk import.
ImportRowOutcome = str  # "inserted" | "updated" | "unchanged"


async def upsert_imported_schedule(
    *,
    session: AsyncSession,
    project_id: UUID,
    data: dict[str, Any],
) -> ImportRowOutcome:
    """Insert or update one schedule from a migration importer payload.

    Identity is ``(project_id, scheduler, name)``. The same name can
    legitimately exist for two different schedulers in one project
    (e.g. the operator runs celery-beat AND z4j-scheduler in the
    same project during a migration cutover) so the scheduler field
    must participate in the conflict key.

    Idempotency: when the row already exists with the same
    ``source_hash``, this is a no-op and returns ``"unchanged"`` so
    the operator can re-run ``z4j-scheduler import`` and only see
    actual diffs land in the audit trail.

    Returns ``"inserted"`` / ``"updated"`` / ``"unchanged"`` so the
    caller can summarise per-batch counts in the API response.

    Note: the caller is responsible for the surrounding transaction
    boundary - we do not commit here. The bulk import endpoint
    commits once after iterating the whole batch so a partial-write
    failure rolls back cleanly.
    """
    from z4j_brain.persistence.enums import ScheduleKind

    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("imported schedule has empty name")

    scheduler = str(data.get("scheduler", "z4j-scheduler"))
    engine = str(data.get("engine", "celery"))
    task_name = str(data.get("task_name", ""))
    if not task_name:
        raise ValueError(f"imported schedule {name!r} has empty task_name")

    kind_raw = str(data.get("kind", "interval"))
    try:
        kind = ScheduleKind(kind_raw)
    except ValueError as exc:
        raise ValueError(
            f"imported schedule {name!r}: unknown kind {kind_raw!r}",
        ) from exc

    # Source-hash governs idempotency. If the operator's tooling
    # forgot to compute it, treat each import as fresh content
    # (we still write the row, just don't get the noop benefit).
    source_hash = data.get("source_hash") or None
    source = data.get("source") or "imported"

    new_values: dict[str, Any] = {
        "engine": engine,
        "task_name": task_name,
        "kind": kind,
        "expression": str(data.get("expression", "")),
        "timezone": str(data.get("timezone", "UTC")) or "UTC",
        "queue": data.get("queue"),
        "args": data.get("args") or [],
        "kwargs": data.get("kwargs") or {},
        "is_enabled": bool(data.get("is_enabled", True)),
        "catch_up": str(data.get("catch_up", "skip")),
        "source": source,
        "source_hash": source_hash,
    }

    result = await session.execute(
        select(Schedule).where(
            Schedule.project_id == project_id,
            Schedule.scheduler == scheduler,
            Schedule.name == name,
        ),
    )
    existing = result.scalar_one_or_none()

    if existing is None:
        row = Schedule(
            project_id=project_id,
            scheduler=scheduler,
            name=name,
            **new_values,
        )
        session.add(row)
        await session.flush()
        return "inserted"

    # Re-import idempotency: same content hash AND existing row also
    # has a hash means nothing changed. We still touch updated_at?
    # No - keeping updated_at stable lets WatchSchedules treat the
    # noop as truly noop (it polls updated_at to detect diffs).
    if source_hash and existing.source_hash == source_hash:
        return "unchanged"

    for key, value in new_values.items():
        setattr(existing, key, value)
    existing.updated_at = datetime.now(UTC)
    await session.flush()
    return "updated"


__all__ = ["ScheduleRepository", "upsert_imported_schedule"]
