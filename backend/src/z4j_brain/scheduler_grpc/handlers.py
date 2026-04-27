"""Per-RPC handler implementations for the brain-side ``SchedulerService``.

The brain implements five of the six RPCs declared in
``packages/z4j-scheduler/proto/scheduler.proto``:

- :rpc:`ListSchedules` - server-streaming initial sync
- :rpc:`WatchSchedules` - server-streaming live diffs
- :rpc:`FireSchedule` - unary; creates a Command via ``CommandDispatcher``
- :rpc:`AcknowledgeFireResult` - unary; updates ``schedules.last_run_at``
- :rpc:`Ping` - unary liveness

The sixth RPC (:rpc:`TriggerSchedule`) lives on the scheduler side -
brain is the gRPC client for that one.

Per ``docs/SCHEDULER.md §13.2``, every state-changing RPC writes an
audit row through the existing HMAC-chained ``audit_log``. Pure read
RPCs (List/Watch/Ping) skip the audit because the scheduler reads
the same data on every reconnect; auditing each one would balloon
the log without operator value.

Phase 1 implementation notes:

- :rpc:`WatchSchedules` polls ``schedules.updated_at`` every
  ``Z4J_SCHEDULER_GRPC_WATCH_POLL_SECONDS`` (default 2s) and emits
  diff events. A future enhancement bolts on Postgres ``LISTEN`` for
  push semantics, but polling at 2s is well within the 100ms-target
  cache freshness budget when amortized against the scheduler's own
  tick cadence (250ms).
- :rpc:`FireSchedule` re-uses ``_pick_scheduler_agent`` from the REST
  handler so brain stays single-source-of-truth on agent selection.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, AsyncIterator
from uuid import UUID

import grpc
from google.protobuf.timestamp_pb2 import Timestamp

from z4j_brain.scheduler_grpc.proto import scheduler_pb2 as pb
from z4j_brain.scheduler_grpc.proto import scheduler_pb2_grpc as pb_grpc

if TYPE_CHECKING:  # pragma: no cover
    from z4j_brain.domain.audit_service import AuditService
    from z4j_brain.domain.command_dispatcher import CommandDispatcher
    from z4j_brain.persistence.database import DatabaseManager
    from z4j_brain.persistence.models import Schedule
    from z4j_brain.settings import Settings

logger = logging.getLogger("z4j.brain.scheduler_grpc.handlers")


# Action string the agent receives when the scheduler asks for a
# fire. Mirrors the REST ``trigger_now`` action but tagged separately
# so the audit log can distinguish "scheduled tick" from "operator
# clicked trigger now".
_FIRE_ACTION = "schedule.fire"

# Default page size when the scheduler does not specify one.
_DEFAULT_LIST_PAGE_SIZE = 100

# Sentinel scheduler-name brain expects in the ``schedules.scheduler``
# column for rows that are managed by z4j-scheduler. Other rows
# (e.g. ``celery-beat`` rows from the agent-side mirror) are NOT
# returned to z4j-scheduler so the two scheduling surfaces don't
# step on each other.
_SCHEDULER_NAME = "z4j-scheduler"


# =====================================================================
# Service implementation
# =====================================================================


class SchedulerServiceImpl(pb_grpc.SchedulerServiceServicer):
    """gRPC servicer wired to brain's existing domain services.

    Construction takes the same singletons the REST routers use so
    there is one consistent path from "wire request arrives" to "row
    in the database is mutated". No new transactional code lives
    here; every handler opens its own ``async with db.session()`` and
    delegates to the shared repositories.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        db: DatabaseManager,
        command_dispatcher: CommandDispatcher,
        audit_service: AuditService,
    ) -> None:
        self._settings = settings
        self._db = db
        self._dispatcher = command_dispatcher
        self._audit = audit_service
        # Used by Watch handler so multiple concurrent streams share
        # one polling loop's snapshot when load matters. Phase 1
        # implementation just keeps the lock to make the code shape
        # ready for that optimisation; each stream still polls
        # independently.
        self._watch_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # ListSchedules - server streaming
    # ------------------------------------------------------------------

    async def ListSchedules(  # noqa: N802 - gRPC-generated name
        self,
        request: pb.ListSchedulesRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[pb.Schedule]:
        from sqlalchemy import select

        from z4j_brain.persistence.models import Schedule

        page_size = (
            request.page_size if request.page_size > 0 else _DEFAULT_LIST_PAGE_SIZE
        )

        async with self._db.session() as session:
            stmt = select(Schedule).where(
                Schedule.scheduler == _SCHEDULER_NAME,
            )
            if request.project_id:
                try:
                    pid = UUID(request.project_id)
                except ValueError:
                    await context.abort(
                        grpc.StatusCode.INVALID_ARGUMENT,
                        f"invalid project_id {request.project_id!r}",
                    )
                    return
                stmt = stmt.where(Schedule.project_id == pid)
            stmt = stmt.order_by(Schedule.id)

            offset = 0
            while True:
                page = stmt.offset(offset).limit(page_size)
                result = await session.execute(page)
                rows = list(result.scalars().all())
                if not rows:
                    break
                for row in rows:
                    yield _schedule_to_pb(row)
                if len(rows) < page_size:
                    break
                offset += page_size

    # ------------------------------------------------------------------
    # WatchSchedules - server streaming
    # ------------------------------------------------------------------

    async def WatchSchedules(  # noqa: N802
        self,
        request: pb.WatchSchedulesRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[pb.ScheduleEvent]:
        """Stream create/update/delete events to the scheduler.

        Two implementations, picked at runtime based on the DB
        dialect:

        - **Postgres**: dedicated asyncpg connection LISTENing on
          ``z4j_schedules_changed`` (set up by migration
          ``2026_04_27_0007_sched_notify``). Sub-100ms cache
          freshness, near-zero idle CPU. Phase 3 default.
        - **SQLite**: polls ``schedules.updated_at`` every
          ``Z4J_SCHEDULER_GRPC_WATCH_POLL_SECONDS`` and emits diffs
          (Phase 1/2 path; SQLite has no LISTEN/NOTIFY). Used by the
          test fixtures + single-tenant evaluation deployments.

        The ``resume_token`` is the ISO timestamp of the latest
        ``updated_at`` the scheduler has seen; on reconnect the
        scheduler echoes it back so the first cycle skips events
        already delivered.
        """
        project_id: UUID | None = None
        if request.project_id:
            try:
                project_id = UUID(request.project_id)
            except ValueError:
                await context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    f"invalid project_id {request.project_id!r}",
                )
                return

        # Dispatch on dialect. The async engine carries the dialect
        # name; we read it once at stream open. Falling back to the
        # poll path on any dialect we don't recognise (defence in
        # depth - a future Postgres replacement should not silently
        # drop notifications because of a typo).
        dialect = self._db.engine.dialect.name
        if dialect == "postgresql":
            async for event in self._watch_via_listen(
                project_id=project_id,
                resume_token=request.resume_token,
                context=context,
            ):
                yield event
            return
        # SQLite + everything else → polling fallback.
        async for event in self._watch_via_polling(
            project_id=project_id,
            resume_token=request.resume_token,
            context=context,
        ):
            yield event

    async def _watch_via_listen(
        self,
        *,
        project_id: UUID | None,
        resume_token: str,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[pb.ScheduleEvent]:
        """Postgres LISTEN/NOTIFY-driven WatchSchedules implementation.

        Opens a dedicated asyncpg connection (LISTEN cannot be
        pooled - the listener identity is bound to the connection),
        subscribes to ``z4j_schedules_changed``, and emits gRPC
        events as notifications arrive.

        Catch-up on connect: if the scheduler sent a ``resume_token``
        we run one diff pass to deliver any events the scheduler
        missed during reconnect, then enter the live LISTEN loop.

        The dedicated connection is closed when the gRPC stream
        ends (client disconnect, scheduler shutdown, transient
        network drop).
        """
        try:
            import asyncpg  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            # asyncpg is a hard dep of brain on Postgres - this branch
            # only fires if the operator stripped it out for some
            # reason. Fall back to polling.
            logger.warning(
                "z4j.brain.scheduler_grpc: asyncpg missing; falling "
                "back to polling for WatchSchedules",
            )
            async for event in self._watch_via_polling(
                project_id=project_id,
                resume_token=resume_token,
                context=context,
            ):
                yield event
            return

        # Catch-up pass: emit any events newer than resume_token so
        # the reconnecting scheduler doesn't miss diffs that landed
        # while the stream was down.
        last_seen_at: datetime | None = None
        if resume_token:
            try:
                last_seen_at = datetime.fromisoformat(resume_token)
            except ValueError:
                logger.warning(
                    "z4j.brain.scheduler_grpc: ignoring malformed "
                    "resume_token %r; starting from current state",
                    resume_token,
                )
        if last_seen_at is not None:
            catchup_events, _ = await self._compute_watch_diff(
                project_id=project_id,
                last_seen_at=last_seen_at,
                snapshot={},
                first_cycle=True,
            )
            for event in catchup_events:
                yield event

        # Open a dedicated asyncpg connection on the same DSN as the
        # SQLAlchemy engine. Translate the SQLAlchemy URL to the
        # asyncpg DSN form (asyncpg doesn't understand the
        # ``postgresql+asyncpg://`` driver suffix). Use
        # ``render_as_string(hide_password=False)`` because the
        # default ``str(url)`` masks the password as ``***`` -
        # asyncpg would then fail with InvalidPasswordError. The
        # rendered string never leaves this function.
        dsn = self._db.engine.url.render_as_string(
            hide_password=False,
        ).replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(
            dsn,
            server_settings={"application_name": "z4j-brain-watch-stream"},
        )
        notification_queue: asyncio.Queue = asyncio.Queue()

        def _on_notify(_conn, _pid, _channel, payload: str) -> None:
            # Called by asyncpg in the connection's task. Just
            # enqueue - the consumer below does the actual work.
            try:
                notification_queue.put_nowait(payload)
            except asyncio.QueueFull:  # pragma: no cover - unbounded queue
                pass

        await conn.add_listener("z4j_schedules_changed", _on_notify)
        try:
            while not context.cancelled():
                try:
                    payload = await asyncio.wait_for(
                        notification_queue.get(), timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    # Liveness ping - keeps the gRPC stream alive
                    # under no-traffic conditions and lets us notice
                    # context cancellation without blocking forever.
                    continue
                except asyncio.CancelledError:
                    return

                event = await self._notification_to_event(
                    payload=payload, project_id=project_id,
                )
                if event is not None:
                    yield event
        finally:
            try:
                await conn.remove_listener(
                    "z4j_schedules_changed", _on_notify,
                )
            except Exception:  # noqa: BLE001
                pass
            try:
                await conn.close()
            except Exception:  # noqa: BLE001
                pass

    async def _watch_via_polling(
        self,
        *,
        project_id: UUID | None,
        resume_token: str,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[pb.ScheduleEvent]:
        """SQLite (and fallback) polling implementation.

        Extracted from the original WatchSchedules body so the
        Postgres path can defer to it on asyncpg failure.
        """
        last_seen_at: datetime | None = None
        if resume_token:
            try:
                last_seen_at = datetime.fromisoformat(resume_token)
            except ValueError:
                logger.warning(
                    "z4j.brain.scheduler_grpc: ignoring malformed "
                    "resume_token %r; starting from current state",
                    resume_token,
                )

        snapshot: dict[UUID, datetime] = {}
        first_cycle = True
        poll_seconds = float(
            self._settings.scheduler_grpc_watch_poll_seconds,
        )

        while not context.cancelled():
            try:
                events, snapshot = await self._compute_watch_diff(
                    project_id=project_id,
                    last_seen_at=last_seen_at,
                    snapshot=snapshot,
                    first_cycle=first_cycle,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "z4j.brain.scheduler_grpc: watch poll crashed",
                )
                await asyncio.sleep(poll_seconds)
                continue

            for event in events:
                yield event
                if event.resume_token:
                    try:
                        last_seen_at = datetime.fromisoformat(
                            event.resume_token,
                        )
                    except ValueError:
                        pass

            first_cycle = False
            try:
                await asyncio.sleep(poll_seconds)
            except asyncio.CancelledError:
                return

    async def _notification_to_event(
        self,
        *,
        payload: str,
        project_id: UUID | None,
    ) -> pb.ScheduleEvent | None:
        """Convert one NOTIFY payload to a ScheduleEvent.

        Payload shape (from migration 2026_04_27_0007):
            {"op": "insert"|"update"|"delete", "id": <uuid>,
             "project_id": <uuid>}

        Returns ``None`` to skip emission when:
        - JSON is malformed
        - the row's project doesn't match this stream's filter
        - the row turns out to belong to a different scheduler
          (we only emit for ``scheduler='z4j-scheduler'``)
        """
        import json as _json  # noqa: PLC0415

        try:
            data = _json.loads(payload)
        except _json.JSONDecodeError:
            logger.warning(
                "z4j.brain.scheduler_grpc: dropped malformed NOTIFY %r",
                payload,
            )
            return None

        try:
            row_id = UUID(str(data["id"]))
            row_project_id = UUID(str(data["project_id"]))
        except (KeyError, ValueError):
            return None

        if project_id is not None and row_project_id != project_id:
            return None

        op_kind = data.get("op")
        if op_kind == "delete":
            return pb.ScheduleEvent(
                kind=pb.ScheduleEvent.Kind.DELETED,
                deleted_id=str(row_id),
                resume_token=datetime.now(UTC).isoformat(),
            )

        # INSERT / UPDATE: fetch the row to build the full payload.
        # The trigger fires on every INSERT/UPDATE regardless of
        # scheduler value; filter here so we don't leak rows owned
        # by celery-beat etc. into the z4j-scheduler stream.
        from sqlalchemy import select  # noqa: PLC0415

        from z4j_brain.persistence.models import Schedule  # noqa: PLC0415

        async with self._db.session() as session:
            result = await session.execute(
                select(Schedule).where(
                    Schedule.id == row_id,
                    Schedule.scheduler == _SCHEDULER_NAME,
                ),
            )
            row = result.scalar_one_or_none()
        if row is None:
            return None

        kind = (
            pb.ScheduleEvent.Kind.CREATED
            if op_kind == "insert"
            else pb.ScheduleEvent.Kind.UPDATED
        )
        return pb.ScheduleEvent(
            kind=kind,
            schedule=_schedule_to_pb(row),
            resume_token=row.updated_at.isoformat(),
        )

    async def _compute_watch_diff(
        self,
        *,
        project_id: UUID | None,
        last_seen_at: datetime | None,
        snapshot: dict[UUID, datetime],
        first_cycle: bool,
    ) -> tuple[list[pb.ScheduleEvent], dict[UUID, datetime]]:
        """Read the current schedule set and emit diff events.

        Returns the events to yield and the new snapshot.
        """
        from sqlalchemy import select

        from z4j_brain.persistence.models import Schedule

        async with self._db.session() as session:
            stmt = select(Schedule).where(
                Schedule.scheduler == _SCHEDULER_NAME,
            )
            if project_id is not None:
                stmt = stmt.where(Schedule.project_id == project_id)
            result = await session.execute(stmt)
            rows = list(result.scalars().all())

        new_snapshot = {row.id: row.updated_at for row in rows}
        events: list[pb.ScheduleEvent] = []

        for row in rows:
            previous = snapshot.get(row.id)
            if previous is None:
                # New row. On the first cycle with a resume_token we
                # only emit rows whose updated_at is strictly after
                # the token (avoid replaying state the scheduler
                # already has). Without a token we emit nothing on
                # first cycle - the scheduler runs ListSchedules for
                # the bulk load path.
                if first_cycle:
                    if last_seen_at is None:
                        continue
                    if row.updated_at <= last_seen_at:
                        continue
                events.append(
                    pb.ScheduleEvent(
                        kind=pb.ScheduleEvent.Kind.CREATED,
                        schedule=_schedule_to_pb(row),
                        resume_token=row.updated_at.isoformat(),
                    ),
                )
            elif row.updated_at > previous:
                events.append(
                    pb.ScheduleEvent(
                        kind=pb.ScheduleEvent.Kind.UPDATED,
                        schedule=_schedule_to_pb(row),
                        resume_token=row.updated_at.isoformat(),
                    ),
                )

        # Detect deletes: anything in the previous snapshot that is
        # absent from new_snapshot.
        for sid in snapshot.keys() - new_snapshot.keys():
            events.append(
                pb.ScheduleEvent(
                    kind=pb.ScheduleEvent.Kind.DELETED,
                    deleted_id=str(sid),
                    resume_token=datetime.now(UTC).isoformat(),
                ),
            )

        return events, new_snapshot

    # ------------------------------------------------------------------
    # FireSchedule
    # ------------------------------------------------------------------

    async def FireSchedule(  # noqa: N802
        self,
        request: pb.FireScheduleRequest,
        context: grpc.aio.ServicerContext,
    ) -> pb.FireScheduleResponse:
        try:
            schedule_id = UUID(request.schedule_id)
            fire_id = UUID(request.fire_id)
        except ValueError as exc:
            return pb.FireScheduleResponse(
                error_code="invalid_request",
                error_message=str(exc),
            )

        from z4j_brain.persistence.repositories import (
            AuditLogRepository,
            CommandRepository,
            ScheduleRepository,
        )

        async with self._db.session() as session:
            schedules = ScheduleRepository(session)
            schedule = await schedules.get(schedule_id)
            if schedule is None:
                return pb.FireScheduleResponse(
                    error_code="schedule_not_found",
                    error_message=(
                        f"schedule {schedule_id} not in brain"
                    ),
                )
            if not schedule.is_enabled:
                # Scheduler should have skipped this on its side, but
                # defend against a race between disable + tick.
                return pb.FireScheduleResponse(
                    error_code="schedule_disabled",
                    error_message="schedule is disabled",
                )

            agent = await _pick_scheduler_agent_for_fire(
                session=session,
                schedule=schedule,
            )
            if agent is None:
                # No matching online agent. Phase 2: instead of
                # surfacing ``agent_offline`` immediately we buffer
                # the fire in ``pending_fires`` so the replay worker
                # can deliver it the moment a matching agent comes
                # online. The scheduler's ``catch_up`` policy still
                # governs replay behaviour at agent-online time:
                # ``skip`` drops, ``fire_one_missed`` keeps only the
                # latest, ``fire_all_missed`` drains the queue.
                #
                # Schedules with ``catch_up='skip'`` still get a row
                # written - the replay worker observes the policy at
                # replay time and drops them. Writing the row keeps
                # the buffer-depth metric honest ("we noticed an
                # outage"); a feature-flag operator who genuinely
                # wants zero buffering can disable buffering by
                # setting Z4J_PENDING_FIRES_BUFFER_SKIP_POLICY=false
                # (Phase 3 op knob; default True today).
                from datetime import UTC, datetime, timedelta  # noqa: PLC0415

                from z4j_brain.persistence.repositories import (  # noqa: PLC0415
                    PendingFiresRepository,
                )

                pending = PendingFiresRepository(session)
                retention_days = self._settings.pending_fires_retention_days
                expires_at = datetime.now(UTC) + timedelta(days=retention_days)
                scheduled_for_dt = (
                    datetime.fromtimestamp(
                        request.scheduled_for.seconds
                        + request.scheduled_for.nanos / 1e9,
                        tz=UTC,
                    )
                    if request.scheduled_for.seconds
                    else datetime.now(UTC)
                )
                await pending.buffer(
                    fire_id=fire_id,
                    schedule_id=schedule.id,
                    project_id=schedule.project_id,
                    engine=schedule.engine,
                    payload={
                        "schedule_id": str(schedule.id),
                        "schedule_name": schedule.name,
                        "task_name": schedule.task_name,
                        "engine": schedule.engine,
                        "queue": schedule.queue,
                        "args": schedule.args,
                        "kwargs": schedule.kwargs,
                        "fire_id": str(fire_id),
                        "scheduled_for": _ts_iso(request.scheduled_for),
                        "fired_at": _ts_iso(request.fired_at),
                    },
                    scheduled_for=scheduled_for_dt,
                    expires_at=expires_at,
                )
                await session.commit()
                return pb.FireScheduleResponse(buffered=True)

            audit_log = AuditLogRepository(session)
            commands = CommandRepository(session)
            try:
                command = await self._dispatcher.issue(
                    commands=commands,
                    audit_log=audit_log,
                    project_id=schedule.project_id,
                    agent_id=agent.id,
                    action=_FIRE_ACTION,
                    target_type="schedule",
                    target_id=str(schedule.id),
                    payload={
                        "schedule_id": str(schedule.id),
                        "schedule_name": schedule.name,
                        "task_name": schedule.task_name,
                        "engine": schedule.engine,
                        "queue": schedule.queue,
                        "args": schedule.args,
                        "kwargs": schedule.kwargs,
                        "fire_id": str(fire_id),
                        "scheduled_for": _ts_iso(request.scheduled_for),
                        "fired_at": _ts_iso(request.fired_at),
                    },
                    issued_by=None,
                    ip=None,
                    user_agent=None,
                    idempotency_key=f"schedule:{schedule.id}:fire:{fire_id}",
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "z4j.brain.scheduler_grpc: FireSchedule failed",
                    extra={
                        "schedule_id": str(schedule_id),
                        "fire_id": str(fire_id),
                    },
                )
                return pb.FireScheduleResponse(
                    error_code="brain_error",
                    error_message=str(exc),
                )

            # Stash the fire_id on the schedule row so
            # AcknowledgeFireResult can correlate. Best-effort - the
            # ack handler can also infer correlation from fire_id
            # alone via the commands table's idempotency_key.
            schedule.last_fire_id = fire_id  # type: ignore[attr-defined]
            await session.commit()

            return pb.FireScheduleResponse(
                command_id=str(command.id),
            )

    # ------------------------------------------------------------------
    # AcknowledgeFireResult
    # ------------------------------------------------------------------

    async def AcknowledgeFireResult(  # noqa: N802
        self,
        request: pb.AcknowledgeFireResultRequest,
        context: grpc.aio.ServicerContext,
    ) -> pb.AcknowledgeFireResultResponse:
        """Update the schedule row to reflect a completed fire.

        The scheduler calls this after it observes (or gives up
        waiting for) the fire result. We update ``last_run_at``,
        bump ``total_runs``, and clear ``last_fire_id`` to make
        room for the next fire.

        The scheduler also re-computes ``next_run_at`` and writes it
        back here so the brain row stays accurate for dashboard
        display - even though the scheduler is the authoritative
        source for "what fires next", the brain shows it on the
        schedule detail page.
        """
        try:
            fire_id = UUID(request.fire_id)
        except ValueError as exc:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"invalid fire_id: {exc}",
            )
            return pb.AcknowledgeFireResultResponse()

        from sqlalchemy import select, update

        from z4j_brain.persistence.models import Schedule

        async with self._db.session() as session:
            # Find the schedule by last_fire_id - the scheduler stamps
            # this when it issues FireSchedule. If we can't find it
            # the fire predates a brain restart or the scheduler is
            # acking a fire from a different scheduler instance; ack
            # is still accepted (scheduler treats ack as best-effort
            # but expects success) but we don't update any row.
            result = await session.execute(
                select(Schedule).where(Schedule.last_fire_id == fire_id),
            )
            schedule = result.scalar_one_or_none()
            if schedule is None:
                logger.info(
                    "z4j.brain.scheduler_grpc: ack for unknown fire_id %s "
                    "(probably a fire from a previous brain process)",
                    fire_id,
                )
                return pb.AcknowledgeFireResultResponse()

            now = datetime.now(UTC)
            updates: dict[str, Any] = {
                "last_run_at": now,
                "updated_at": now,
                "last_fire_id": None,
            }
            if request.status == "success":
                updates["total_runs"] = (schedule.total_runs or 0) + 1

            await session.execute(
                update(Schedule)
                .where(Schedule.id == schedule.id)
                .values(**updates),
            )
            await session.commit()

        return pb.AcknowledgeFireResultResponse()

    # ------------------------------------------------------------------
    # Ping
    # ------------------------------------------------------------------

    async def Ping(  # noqa: N802
        self,
        request: pb.PingRequest,
        context: grpc.aio.ServicerContext,
    ) -> pb.PingResponse:
        from z4j_brain import __version__

        ts = Timestamp()
        ts.FromDatetime(datetime.now(UTC))
        return pb.PingResponse(
            brain_version=__version__,
            brain_time=ts,
        )


# =====================================================================
# Helpers
# =====================================================================


def _schedule_to_pb(schedule: Schedule) -> pb.Schedule:
    """Translate a SQLAlchemy ``Schedule`` row to its protobuf form."""
    last_run = Timestamp()
    if schedule.last_run_at is not None:
        last_run.FromDatetime(schedule.last_run_at)
    next_run = Timestamp()
    if schedule.next_run_at is not None:
        next_run.FromDatetime(schedule.next_run_at)

    # JSONB columns may be dict/list - serialise to bytes for the
    # protobuf ``bytes`` fields.
    args_json = json.dumps(schedule.args or []).encode()
    kwargs_json = json.dumps(schedule.kwargs or {}).encode()

    # Optional fields added by the scheduler-columns migration may
    # not be present on rows from a pre-migration brain (defensive
    # ``getattr``).
    catch_up = getattr(schedule, "catch_up", None) or "skip"
    source = getattr(schedule, "source", None) or "dashboard"
    source_hash = getattr(schedule, "source_hash", None) or ""

    return pb.Schedule(
        id=str(schedule.id),
        project_id=str(schedule.project_id),
        engine=schedule.engine,
        name=schedule.name,
        task_name=schedule.task_name,
        kind=schedule.kind.value if hasattr(schedule.kind, "value") else str(schedule.kind),
        expression=schedule.expression,
        timezone=schedule.timezone or "UTC",
        queue=schedule.queue or "",
        args_json=args_json,
        kwargs_json=kwargs_json,
        is_enabled=bool(schedule.is_enabled),
        catch_up=catch_up,
        source=source,
        last_run_at=last_run,
        next_run_at=next_run,
        total_runs=int(schedule.total_runs or 0),
        source_hash=source_hash,
    )


def _ts_iso(ts: Timestamp) -> str:
    """Render a Timestamp as ISO-8601 for a Command payload."""
    if ts.seconds == 0 and ts.nanos == 0:
        return ""
    return datetime.fromtimestamp(
        ts.seconds + ts.nanos / 1_000_000_000, tz=UTC,
    ).isoformat()


async def _pick_scheduler_agent_for_fire(
    *,
    session: Any,
    schedule: Schedule,
) -> Any:
    """Pick an online agent that can run the schedule's engine.

    Returns ``None`` if no match exists. Mirrors the REST handler's
    ``_pick_scheduler_agent`` but filters on ``engine_adapters``
    (the engine name that will actually run the task) rather than
    ``scheduler_adapters`` - z4j-scheduler is the scheduler, the
    agent only needs the engine to execute the task.
    """
    from z4j_brain.persistence.repositories import AgentRepository

    agents = await AgentRepository(session).list_online_for_project(
        schedule.project_id,
    )
    for agent in agents:
        if schedule.engine in (agent.engine_adapters or ()):
            return agent
    return None


__all__ = ["SchedulerServiceImpl"]
