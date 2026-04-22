"""``schedules`` table - periodic / cron / clocked / solar schedules."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from z4j_brain.persistence.base import Base
from z4j_brain.persistence.enums import ScheduleKind, TaskPriority
from z4j_brain.persistence.models._mixins import PKMixin, TimestampsMixin
from z4j_brain.persistence.types import big_integer, jsonb


class Schedule(PKMixin, TimestampsMixin, Base):
    """A periodic schedule managed by the brain.

    Schedules are read by the SchedulerAdapter (e.g.
    ``z4j-celerybeat``) on the agent and synced bidirectionally -
    creating a row here pushes the change to the agent, while
    schedules created in the customer's own scheduler appear via the
    ``schedule.created`` event stream.

    Attributes:
        project_id: Owning project. ``ON DELETE CASCADE``.
        engine: Engine adapter the schedule's task runs on.
        scheduler: Scheduler adapter that owns the schedule
            (``celery-beat``, ``apscheduler``, ...).
        name: Schedule name. Unique per ``(project, scheduler)``.
        task_name: Fully-qualified task name to invoke.
        kind: ``cron`` / ``interval`` / ``solar`` / ``clocked``.
        expression: Schedule-kind-specific expression - cron string,
            interval seconds, solar event, ISO timestamp.
        timezone: Schedule timezone.
        queue: Optional queue override.
        args / kwargs: Task arguments at fire time.
        is_enabled: Soft-disable flag.
        last_run_at / next_run_at: Last and next firing times.
        total_runs: Lifetime fire count.
        external_id: Engine-native id (e.g. django-celery-beat
            ``PeriodicTask.id``) so the agent can resolve back.
    """

    __tablename__ = "schedules"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    engine: Mapped[str] = mapped_column(String(40), nullable=False)
    scheduler: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    task_name: Mapped[str] = mapped_column(String(500), nullable=False)
    kind: Mapped[ScheduleKind] = mapped_column(
        Enum(
            ScheduleKind,
            name="schedule_kind",
            native_enum=True,
            create_type=True,
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=False,
    )
    expression: Mapped[str] = mapped_column(String, nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC",
    )
    queue: Mapped[str | None] = mapped_column(String(200), nullable=True)
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(
            TaskPriority,
            name="task_priority",
            native_enum=True,
            create_type=False,  # Already created by the task_priority migration.
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=False,
        default=TaskPriority.NORMAL,
        server_default="normal",
    )
    args: Mapped[Any] = mapped_column(
        jsonb(), nullable=False, default=list, server_default="[]",
    )
    kwargs: Mapped[Any] = mapped_column(
        jsonb(), nullable=False, default=dict, server_default="{}",
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    total_runs: Mapped[int] = mapped_column(
        big_integer(), nullable=False, default=0, server_default="0",
    )
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "project_id", "scheduler", "name",
            name="uq_schedules_project_scheduler_name",
        ),
        Index("ix_schedules_project_id", "project_id"),
        # The partial idx WHERE is_enabled is added in the migration
        # - SQLAlchemy supports partial indexes via postgresql_where
        # but only on Postgres, and emitting it on SQLite is a
        # syntax error.
    )


__all__ = ["Schedule"]
