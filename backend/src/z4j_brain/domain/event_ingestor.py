"""Event ingestion: agent → events table → tasks projection.

The :class:`EventIngestor` is the brain-side counterpart of the
agent's event capture path. For each event in an inbound
``event_batch``:

1. Re-apply the redaction engine (defense in depth - the agent
   already redacted, but if the agent is misconfigured or
   compromised we MUST scrub before storage).
2. INSERT into the partitioned ``events`` table. Idempotent on
   ``(occurred_at, id)`` so a re-connecting agent that replays
   buffered events does not duplicate.
3. Project the event onto the ``tasks`` table - upsert by
   ``(project_id, engine, task_id)``, applying the right state
   transition + lifecycle timestamps for the event kind.
4. Touch the ``queues`` table if the event mentions a queue we
   have not yet recorded.
5. Bump the agent's ``last_seen_at`` (event traffic counts as a
   heartbeat).

The class is dependency-injected with a :class:`RedactionEngine`
plus the four repositories it writes to. No SQLAlchemy imports,
no FastAPI imports, no implicit globals.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4, uuid5

import structlog

from z4j_core.models.event import EventKind
from z4j_core.redaction import RedactionEngine

from z4j_brain.persistence.enums import TaskPriority, TaskState

#: Namespace UUID used to derive the brain-side event id from the
#: agent-supplied id + project_id. Generated once via
#: ``uuid.uuid4()`` and pinned here so the same agent_event_id
#: under the same project_id always derives the same brain-side
#: id (idempotent across replays) but DIFFERENT project_ids
#: cannot ever collide on the same brain-side id (closes the
#: cross-project censorship vector - R3 finding H1).
_EVENT_ID_NAMESPACE = UUID("c4d2c84e-2f0a-4b6c-9c5b-1d6f9a1e7c2a")

#: Bounds for the ``occurred_at`` clamp. We accept events up to
#: this far in the past or future relative to brain wall-clock;
#: anything outside is clamped to ``now`` with a logged warning.
#: This protects against malicious agents picking an
#: ``occurred_at`` outside the pre-created partition window
#: (which would raise ``no partition of relation "events" found``
#: on Postgres) - R3 finding C2. It also protects against an
#: attacker using a far-future timestamp to dodge dedupe.
_OCCURRED_AT_PAST_LIMIT = timedelta(days=400)
#: Tight future clamp (60s, down from 5 min) so a hostile agent
#: cannot stamp ``task.succeeded`` 4m59s in the future to "lock"
#: a task's state column against every legitimate subsequent
#: event within the window. 60 s is plenty of slack for NTP
#: drift between the agent's clock and the brain's - the
#: ReplayGuard's freshness window is already ±60 s. R5 H1.
_OCCURRED_AT_FUTURE_LIMIT = timedelta(seconds=60)

if TYPE_CHECKING:
    from z4j_brain.persistence.repositories import (
        AgentRepository,
        EventRepository,
        QueueRepository,
        TaskRepository,
        WorkerRepository,
    )


logger = structlog.get_logger("z4j.brain.event_ingestor")


#: Map from agent-side EventKind to the TaskState the brain should
#: project onto the ``tasks`` row. Events whose state mapping is
#: None do not change the task's state column (e.g. heartbeat-only
#: events, schedule events).
_STATE_FOR_KIND: dict[EventKind, TaskState | None] = {
    EventKind.TASK_RECEIVED: TaskState.RECEIVED,
    EventKind.TASK_STARTED: TaskState.STARTED,
    EventKind.TASK_SUCCEEDED: TaskState.SUCCESS,
    EventKind.TASK_FAILED: TaskState.FAILURE,
    EventKind.TASK_RETRIED: TaskState.RETRY,
    EventKind.TASK_REVOKED: TaskState.REVOKED,
}


class EventIngestor:
    """Project agent-side events onto the brain's persistent state."""

    __slots__ = ("_redaction",)

    def __init__(self, redaction: RedactionEngine) -> None:
        self._redaction = redaction

    async def ingest_batch(
        self,
        *,
        events: list[dict[str, Any]],
        project_id: UUID,
        agent_id: UUID,
        agents: "AgentRepository",
        event_repo: "EventRepository",
        task_repo: "TaskRepository",
        queue_repo: "QueueRepository",
        worker_repo: "WorkerRepository | None" = None,
    ) -> int:
        """Ingest a batch of events. Returns the number of NEW rows.

        The full batch participates in the caller's transaction.
        Per-event redaction failures do NOT poison the batch - the
        bad event is logged + skipped, the rest still ingest.
        """
        new_count = 0
        for raw_event in events:
            try:
                if await self._ingest_one(
                    raw_event=raw_event,
                    project_id=project_id,
                    agent_id=agent_id,
                    event_repo=event_repo,
                    task_repo=task_repo,
                    queue_repo=queue_repo,
                    worker_repo=worker_repo,
                ):
                    new_count += 1
            except Exception:  # noqa: BLE001
                logger.exception(
                    "z4j event_ingestor: per-event ingest failed; skipping",
                    project_id=str(project_id),
                    agent_id=str(agent_id),
                )

        # Heartbeat: any event traffic counts as the agent being
        # alive.
        await agents.touch_heartbeat(agent_id)
        return new_count

    async def _ingest_one(
        self,
        *,
        raw_event: dict[str, Any],
        project_id: UUID,
        agent_id: UUID,
        event_repo: "EventRepository",
        task_repo: "TaskRepository",
        queue_repo: "QueueRepository",
        worker_repo: "WorkerRepository | None" = None,
    ) -> bool:
        """Ingest one event. Returns True if a new events row was inserted."""
        # Redaction defense in depth.
        scrubbed = self._redaction.scrub(raw_event)
        if not isinstance(scrubbed, dict):
            return False

        engine = str(scrubbed.get("engine", "")).strip()
        kind_value = str(scrubbed.get("kind", "")).strip()
        task_id = str(scrubbed.get("task_id", "")).strip()
        occurred_at_raw = scrubbed.get("occurred_at")
        data = scrubbed.get("data") or {}

        if not engine or not kind_value:
            return False

        try:
            kind = EventKind(kind_value)
        except ValueError:
            kind = EventKind.UNKNOWN

        occurred_at = _clamp_occurred_at(
            _parse_datetime(occurred_at_raw),
            project_id=project_id,
            agent_id=agent_id,
        )
        # Build the brain-side event id from the agent-supplied id,
        # NAMESPACED BY PROJECT_ID. Two consequences:
        #
        # 1. Replays from a re-connecting agent always derive the
        #    same brain-side id (idempotent - the conflict key on
        #    the partitioned events table fires).
        # 2. Project A and Project B can never collide on the same
        #    brain-side id, even if their agents pick the same
        #    raw uuid. Project-A agent CAN'T censor Project-B's
        #    events by picking known ids (R3 finding H1).
        #
        # If the agent omitted the id (or sent an unparseable /
        # nil / max / non-v4-v7 value - see _coerce_event_id), we
        # mint a fresh uuid4 with a logged warning. Idempotency
        # is lost for that single event but the system stays safe.
        agent_event_id = _coerce_event_id(scrubbed.get("id"))
        if agent_event_id is None:
            event_id = uuid4()
            logger.warning(
                "z4j event_ingestor: agent omitted or sent invalid event id, "
                "minting one (events table dedupe will not work for replays)",
                project_id=str(project_id),
                agent_id=str(agent_id),
            )
        else:
            event_id = uuid5(
                _EVENT_ID_NAMESPACE,
                f"{project_id}:{agent_event_id}",
            )

        # 0) Prometheus counter. Best-effort: a metric-registry
        # hiccup must not break event ingestion. The bump below to
        # ``z4j_swallowed_exceptions_total`` keeps this visible in
        # Grafana even though we don't log per event.
        try:
            from z4j_brain.api.metrics import z4j_events_ingested_total

            z4j_events_ingested_total.labels(
                project=str(project_id), engine=engine, kind=kind_value,
            ).inc()
        except Exception:  # noqa: BLE001
            from z4j_brain.api.metrics import record_swallowed

            record_swallowed("event_ingestor", "counter_inc")

        # 1) Append to the partitioned events table.
        inserted = await event_repo.insert(
            event_id=event_id,
            project_id=project_id,
            agent_id=agent_id,
            engine=engine,
            task_id=task_id,
            kind=kind.value,
            occurred_at=occurred_at,
            payload=data if isinstance(data, dict) else {},
        )

        # 2) Touch the queue if mentioned.
        queue_name = data.get("queue") if isinstance(data, dict) else None
        if isinstance(queue_name, str) and queue_name:
            try:
                await queue_repo.touch(
                    project_id=project_id,
                    engine=engine,
                    name=queue_name,
                )
            except Exception:  # noqa: BLE001
                logger.exception("z4j event_ingestor: queue touch failed")

        # 3) Touch the worker if the event carries a hostname.
        worker_name = data.get("worker") if isinstance(data, dict) else None
        if isinstance(worker_name, str) and worker_name and worker_repo is not None:
            try:
                from z4j_brain.persistence.enums import WorkerState

                await worker_repo.upsert_from_event(
                    project_id=project_id,
                    engine=engine,
                    name=worker_name,
                    updates={
                        "state": WorkerState.ONLINE,
                        "last_heartbeat": occurred_at,
                    },
                )
            except Exception:  # noqa: BLE001
                logger.exception("z4j event_ingestor: worker upsert failed")

        # 4) Project onto tasks (only for task-shaped events).
        if task_id and kind != EventKind.UNKNOWN:
            await self._project_task(
                project_id=project_id,
                engine=engine,
                task_id=task_id,
                kind=kind,
                occurred_at=occurred_at,
                data=data if isinstance(data, dict) else {},
                task_repo=task_repo,
            )

        # 5) Project schedule events onto the schedules table.
        if kind_value in (
            EventKind.SCHEDULE_CREATED.value,
            EventKind.SCHEDULE_UPDATED.value,
        ):
            schedule_data = (
                data.get("schedule") if isinstance(data, dict) else None
            )
            if isinstance(schedule_data, dict):
                try:
                    from z4j_brain.persistence.repositories import (
                        ScheduleRepository,
                    )

                    # Inject the engine + scheduler names from the
                    # outer Event envelope - the inner schedule
                    # payload doesn't carry them (and if it did, the
                    # repo was silently defaulting to "celery" /
                    # "celery-beat" - LATENT-1). Each scheduler
                    # adapter now reports its own name as
                    # ``Event.engine`` so rq-scheduler / apscheduler
                    # will land correctly once they ship.
                    enriched = dict(schedule_data)
                    enriched.setdefault("engine", engine)
                    enriched.setdefault("scheduler", engine)

                    # Re-use the session from the caller's transaction.
                    # The ScheduleRepository is constructed from the
                    # same session passed via the existing repos.
                    schedule_repo = ScheduleRepository(task_repo.session)
                    await schedule_repo.upsert_from_event(
                        project_id=project_id,
                        data=enriched,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "z4j event_ingestor: schedule upsert failed",
                    )

        return inserted

    async def _project_task(
        self,
        *,
        project_id: UUID,
        engine: str,
        task_id: str,
        kind: EventKind,
        occurred_at: datetime,
        data: dict[str, Any],
        task_repo: TaskRepository,
    ) -> None:
        """Apply per-event-kind updates to the ``tasks`` row."""
        # Resolve priority from event data. The agent includes it
        # if the task has ``@z4j_meta(priority="critical")`` etc.
        # Default to NORMAL for tasks without explicit priority.
        priority_raw = data.get("priority")
        try:
            priority = TaskPriority(priority_raw) if priority_raw else TaskPriority.NORMAL
        except ValueError:
            priority = TaskPriority.NORMAL

        # Monotonic-timestamp guard against state regression
        # (external-audit Medium #6). Events can legitimately
        # arrive out of order - a late ``task.started`` after
        # ``task.succeeded`` must NOT move a finished task back
        # to STARTED. We look up the current row's latest
        # lifecycle timestamp; if the incoming event is older,
        # we skip the state transition (other fields like
        # ``worker_name`` / ``exception`` can still be
        # back-filled because they're informational, not
        # lifecycle-bearing).
        existing_task = await task_repo.get_by_engine_task_id(
            project_id=project_id, engine=engine, task_id=task_id,
        )
        existing_latest = _task_latest_lifecycle_at(existing_task)

        defaults: dict[str, Any] = {
            "name": str(data.get("task_name") or "unknown"),
            "queue": (str(data.get("queue")) if data.get("queue") else None),
            "state": TaskState.PENDING,
            "priority": priority,
        }
        updates: dict[str, Any] = {}

        new_state = _STATE_FOR_KIND.get(kind)
        if new_state is not None:
            if (
                existing_latest is not None
                and occurred_at < existing_latest
            ):
                # Stale event - keep the state column alone.
                logger.info(
                    "z4j event_ingestor: dropping out-of-order state transition",
                    project_id=str(project_id),
                    task_id=task_id,
                    event_kind=kind.value,
                    event_at=occurred_at.isoformat(),
                    existing_latest=existing_latest.isoformat(),
                )
            else:
                updates["state"] = new_state

        # Only update priority if explicitly set in the event (don't
        # downgrade a previously-set priority with a default NORMAL
        # from a later event that happens to not carry the field).
        if priority_raw:
            updates["priority"] = priority

        if kind == EventKind.TASK_RECEIVED:
            updates.update({
                "received_at": occurred_at,
                "args": data.get("args"),
                "kwargs": data.get("kwargs"),
                "queue": (str(data.get("queue")) if data.get("queue") else None),
                "name": str(data.get("task_name") or "unknown"),
            })
            # Canvas linkage from Celery's request: ``parent_task_id``
            # is the task that called ``apply_async`` for me;
            # ``root_task_id`` is the original entry point of the
            # chain / group / chord. Persist them so the dashboard
            # can render the dependency tree on the task detail
            # page.
            #
            # Defense against cross-project linkage poisoning: a
            # compromised Project-A agent could otherwise emit a
            # ``task-received`` event with ``parent_task_id``
            # pointing at a known Project-B task id. Reads via
            # ``get_tree`` are project-scoped today, so this would
            # not leak data - but any future query that joins on
            # ``parent_task_id`` without re-applying ``project_id``
            # would mix tenants. We refuse to store a parent /
            # root that already exists under a *different* project;
            # references that don't exist at all are stored as-is
            # to preserve the legitimate out-of-order ingest case
            # (child event arriving before parent).
            parent_task_id = data.get("parent_task_id")
            root_task_id = data.get("root_task_id")
            if parent_task_id:
                clean = await self._sanitize_canvas_ref(
                    project_id=project_id,
                    engine=engine,
                    task_id=task_id,
                    candidate=str(parent_task_id),
                    field="parent_task_id",
                    task_repo=task_repo,
                )
                if clean is not None:
                    updates["parent_task_id"] = clean
            if root_task_id:
                clean = await self._sanitize_canvas_ref(
                    project_id=project_id,
                    engine=engine,
                    task_id=task_id,
                    candidate=str(root_task_id),
                    field="root_task_id",
                    task_repo=task_repo,
                )
                if clean is not None:
                    updates["root_task_id"] = clean
        elif kind == EventKind.TASK_STARTED:
            updates.update({
                "started_at": occurred_at,
                "worker_name": (
                    str(data.get("worker")) if data.get("worker") else None
                ),
            })
        elif kind == EventKind.TASK_SUCCEEDED:
            updates.update({
                "finished_at": occurred_at,
                "result": data.get("result"),
                "runtime_ms": _coerce_int(data.get("runtime_ms")),
                "exception": None,
                "traceback": None,
            })
        elif kind == EventKind.TASK_FAILED:
            updates.update({
                "finished_at": occurred_at,
                "exception": _coerce_str(data.get("exception")),
                "traceback": _coerce_str(data.get("traceback")),
            })
        elif kind == EventKind.TASK_RETRIED:
            updates.update({
                "retry_count": _coerce_int(data.get("retry_count"), default=0)
                or 0,
            })
        elif kind == EventKind.TASK_REVOKED:
            updates.update({
                "finished_at": occurred_at,
            })

        # Prometheus task metrics for terminal states.
        try:
            from z4j_brain.api.metrics import z4j_task_duration_seconds, z4j_tasks_total

            task_name = str(data.get("task_name") or "unknown")
            if kind in (EventKind.TASK_SUCCEEDED, EventKind.TASK_FAILED, EventKind.TASK_REVOKED):
                z4j_tasks_total.labels(
                    project=str(project_id), task_name=task_name, state=kind.value,
                ).inc()
            if kind == EventKind.TASK_SUCCEEDED:
                runtime_ms = _coerce_int(data.get("runtime_ms"))
                if runtime_ms is not None and runtime_ms > 0:
                    z4j_task_duration_seconds.labels(
                        project=str(project_id), task_name=task_name,
                    ).observe(runtime_ms / 1000.0)
        except Exception:  # noqa: BLE001
            # Metric write failed; event ingestion must not block.
            from z4j_brain.api.metrics import record_swallowed

            record_swallowed("event_ingestor", "task_metrics")

        await task_repo.upsert_from_event(
            project_id=project_id,
            engine=engine,
            task_id=task_id,
            defaults=defaults,
            updates=updates,
        )

    async def _sanitize_canvas_ref(
        self,
        *,
        project_id: UUID,
        engine: str,
        task_id: str,
        candidate: str,
        field: str,
        task_repo: TaskRepository,
    ) -> str | None:
        """Validate a parent / root task-id reference before persisting.

        Refuses references that are structurally implausible
        (oversize, self-loop) and references that already belong
        to a *different* project (cross-project linkage poisoning
        - see caller for context). Returns the cleaned value or
        ``None`` to indicate "drop this field from the update".
        """
        # Structural floor: empty string already filtered by caller.
        # Reject oversize values that would silently truncate
        # against the column's String(200), and self-loops.
        if len(candidate) > 200 or "\x00" in candidate:
            logger.warning(
                "z4j event_ingestor: dropped malformed canvas reference",
                project_id=str(project_id), field=field,
            )
            return None
        if candidate == task_id:
            return None  # self-loop; meaningless
        try:
            elsewhere = await task_repo.other_project_owns(
                project_id=project_id, engine=engine, task_id=candidate,
            )
        except Exception:  # noqa: BLE001
            # If the lookup fails for any reason, fall back to
            # storing as-is - we'd rather keep the linkage than
            # silently drop it because of a transient DB hiccup.
            return candidate
        if elsewhere:
            # The (engine, task_id) is unambiguously owned by
            # another project (no row exists in the caller's
            # project). This is the cross-project linkage
            # poisoning case we block. Two projects legitimately
            # sharing a task_id produce ``elsewhere=False`` and
            # the reference is kept - external-audit Medium #5
            # fix for false "cross-project" drops.
            logger.warning(
                "z4j event_ingestor: dropped cross-project canvas reference",
                project_id=str(project_id), field=field,
            )
            return None
        return candidate


def _task_latest_lifecycle_at(task: Any) -> datetime | None:
    """Return the newest lifecycle timestamp on a task row, or None.

    Used by the state-projection monotonic guard - a state
    transition whose ``occurred_at`` predates this value is a
    late / out-of-order event and must not regress the state
    column. We look at ``finished_at`` → ``started_at`` →
    ``received_at`` in that order (most recent lifecycle stage
    wins). Returns None when the task row doesn't exist yet.

    **Defence in depth (R5 H1):** even though the ingest path
    clamps incoming ``occurred_at`` to ``now + 60s``, an older
    row may still carry a timestamp from before the clamp was
    tightened. We apply ``min(ts, now)`` here so the guard can
    never "pin" a task's state by comparing against a future
    timestamp baked into its lifecycle columns.
    """
    if task is None:
        return None
    now = datetime.now(UTC)
    candidates = [
        getattr(task, "finished_at", None),
        getattr(task, "started_at", None),
        getattr(task, "received_at", None),
    ]
    newest: datetime | None = None
    for ts in candidates:
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        # Clamp future-dated lifecycle timestamps - a legacy row
        # (pre-R5) may have future stamps that would otherwise
        # freeze the state column against any legitimate event.
        if ts > now:
            ts = now
        if newest is None or ts > newest:
            newest = ts
    return newest


#: UUID variants we trust as agent-supplied event ids. v4 is the
#: random variant the current agent mints. v7 is the time-ordered
#: variant a future agent may switch to. Versions 1, 2, 3, 5, 8
#: either leak host information or are derived from an external
#: namespace and could collide deliberately if the namespace is
#: known. Nil / max are obviously not random and would let a
#: well-known id be used as a collision pin.
_TRUSTED_UUID_VERSIONS = frozenset({4, 7})


def _coerce_event_id(value: Any) -> UUID | None:
    """Best-effort UUID coercion for the agent-supplied event id.

    Accepts a UUID instance or a string that ``UUID()`` can parse,
    AND requires it to be a v4 or v7 UUID with non-zero / non-max
    integer value. Anything else returns ``None`` so the caller
    can fall back to minting a fresh id with a logged warning.

    Tightened in R3 (finding H2) - the previous version accepted
    nil UUIDs and arbitrary versions, letting an attacker pin
    collision attempts at well-known ids.
    """
    if value is None:
        return None
    if isinstance(value, UUID):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = UUID(value)
        except (ValueError, AttributeError):
            return None
    else:
        return None
    if parsed.int == 0 or parsed.int == (1 << 128) - 1:
        return None
    if parsed.version not in _TRUSTED_UUID_VERSIONS:
        return None
    return parsed


def _clamp_occurred_at(
    value: datetime, *, project_id: UUID, agent_id: UUID,
) -> datetime:
    """Clamp ``occurred_at`` to ``[now - 400d, now + 5min]``.

    Defends against:

    - **DoS via unpartitioned timestamp** (R3 finding C2): the
      partitioned events table only has partitions pre-created
      for a finite window. A timestamp outside that window
      raises ``no partition of relation "events" found`` on
      Postgres, blowing up the ingest. Clamping prevents this
      class of failure structurally.
    - **Dedupe-dodging via far-future ts**: an attacker picking a
      future ``occurred_at`` lands the row in a partition where
      no legitimate event will ever land - defeats the (limited)
      protection of the conflict key.

    Out-of-range values are clamped to ``now`` and a warning is
    logged so misbehaving agents are observable in Grafana.
    """
    now = datetime.now(UTC)
    if value < now - _OCCURRED_AT_PAST_LIMIT:
        logger.warning(
            "z4j event_ingestor: occurred_at clamped (too far in past)",
            project_id=str(project_id), agent_id=str(agent_id),
            received=value.isoformat(),
        )
        return now
    if value > now + _OCCURRED_AT_FUTURE_LIMIT:
        logger.warning(
            "z4j event_ingestor: occurred_at clamped (too far in future)",
            project_id=str(project_id), agent_id=str(agent_id),
            received=value.isoformat(),
        )
        return now
    return value


def _parse_datetime(value: Any) -> datetime:
    """Best-effort ISO-8601 → datetime. Falls back to ``now()``."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


def _coerce_int(value: Any, *, default: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:8192]


__all__ = ["EventIngestor"]
