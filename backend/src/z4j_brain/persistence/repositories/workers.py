"""``workers`` repository."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from z4j_brain.persistence.enums import WorkerState
from z4j_brain.persistence.models import Event, Worker
from z4j_brain.persistence.repositories._base import BaseRepository


#: Event kinds we count per worker. Source of truth is
#: ``z4j_core.models.event.EventKind`` - duplicated here as
#: literals because importing the enum into the brain repo would
#: create a brain → core dep that import-linter forbids in this
#: direction. A drift test in ``tests/unit/test_workers_repo.py``
#: catches any rename in either side.
_KIND_SUCCEEDED = "task.succeeded"
_KIND_FAILED = "task.failed"
_KIND_RETRIED = "task.retried"


class WorkerRepository(BaseRepository[Worker]):
    """Worker process state CRUD."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Worker)

    async def list_for_project(self, project_id: UUID) -> list[Worker]:
        result = await self.session.execute(
            select(Worker)
            .where(Worker.project_id == project_id)
            .order_by(Worker.last_heartbeat.desc().nulls_last(), Worker.name),
        )
        return list(result.scalars().all())

    async def upsert_from_event(
        self,
        *,
        project_id: UUID,
        engine: str,
        name: str,
        defaults: dict[str, Any] | None = None,
        updates: dict[str, Any],
    ) -> Worker:
        """Insert or update by ``(project, engine, name)``.

        Two concurrent events for the same ``(project, engine,
        name)`` race on the SELECT-then-INSERT window below. The
        INSERT wraps in a SAVEPOINT so a ``UniqueViolation``
        rollback is scoped to this one row - the outer transaction
        (which owns the events batch's other writes) survives.
        If the insert loses the race the caller re-reads the now-
        existing row and applies the updates (R4 follow-up: the
        enterprise stack exercised this path under concurrent
        load and caught a ``PendingRollbackError`` cascade).
        """
        from sqlalchemy.exc import IntegrityError

        result = await self.session.execute(
            select(Worker).where(
                Worker.project_id == project_id,
                Worker.engine == engine,
                Worker.name == name,
            ),
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            row = Worker(
                project_id=project_id,
                engine=engine,
                name=name,
                state=(updates.get("state") or WorkerState.UNKNOWN),
                **(defaults or {}),
            )
            for key, value in updates.items():
                setattr(row, key, value)
            try:
                async with self.session.begin_nested():
                    self.session.add(row)
                    await self.session.flush()
            except IntegrityError:
                # Another concurrent event inserted first. Re-read
                # and fall through to the update branch.
                result = await self.session.execute(
                    select(Worker).where(
                        Worker.project_id == project_id,
                        Worker.engine == engine,
                        Worker.name == name,
                    ),
                )
                existing = result.scalar_one()
            else:
                return row
        for key, value in updates.items():
            setattr(existing, key, value)
        await self.session.flush()
        return existing

    async def counts_for_project(
        self, project_id: UUID,
    ) -> dict[str, dict[str, int]]:
        """Return per-worker task counts aggregated from the events table.

        Returns a mapping of ``worker_name -> {processed, succeeded,
        failed, retried}``. Worker names match
        ``events.payload->>'worker'`` which the agent's mapper sets
        from the Celery signal's ``hostname`` (e.g.
        ``celery@web-01``). Workers with zero events are simply
        absent from the dict; the API layer fills zero-defaults so
        the dashboard table stays uniform.

        SQL is dialect-portable: ``payload->>'worker'`` works on
        Postgres + SQLite (the ``->>`` JSON-extract operator is
        supported by both). ``GROUP BY`` keeps the work on the
        database.

        ``processed = succeeded + failed`` (the conventional
        Celery-flower meaning - retries do NOT count as processed
        since the task has not finished). We expose all four so
        the dashboard can render Total / Succeeded / Failed /
        Retried columns separately.
        """
        worker_expr = Event.payload["worker"].astext.label("worker_name")
        succeeded_sum = func.sum(
            case((Event.kind == _KIND_SUCCEEDED, 1), else_=0),
        ).label("succeeded")
        failed_sum = func.sum(
            case((Event.kind == _KIND_FAILED, 1), else_=0),
        ).label("failed")
        retried_sum = func.sum(
            case((Event.kind == _KIND_RETRIED, 1), else_=0),
        ).label("retried")
        stmt = (
            select(worker_expr, succeeded_sum, failed_sum, retried_sum)
            .where(
                Event.project_id == project_id,
                Event.kind.in_(
                    (_KIND_SUCCEEDED, _KIND_FAILED, _KIND_RETRIED),
                ),
                worker_expr.is_not(None),
            )
            .group_by(worker_expr)
        )
        result = await self.session.execute(stmt)
        out: dict[str, dict[str, int]] = {}
        for row in result.all():
            name = row.worker_name
            if not name:
                continue
            succeeded = int(row.succeeded or 0)
            failed = int(row.failed or 0)
            retried = int(row.retried or 0)
            out[name] = {
                "succeeded": succeeded,
                "failed": failed,
                "retried": retried,
                "processed": succeeded + failed,
            }
        return out

    async def touch_heartbeat(
        self,
        *,
        project_id: UUID,
        engine: str,
        name: str,
        when: datetime,
    ) -> None:
        result = await self.session.execute(
            select(Worker).where(
                Worker.project_id == project_id,
                Worker.engine == engine,
                Worker.name == name,
            ),
        )
        worker = result.scalar_one_or_none()
        if worker is None:
            return
        worker.last_heartbeat = when
        worker.state = WorkerState.ONLINE
        await self.session.flush()


__all__ = ["WorkerRepository"]
