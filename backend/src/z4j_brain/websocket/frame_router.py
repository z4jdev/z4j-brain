"""Inbound frame dispatch.

The gateway's receive loop hands every parsed frame to
:meth:`FrameRouter.dispatch`. The router routes by frame type to
the right domain service:

- ``event_batch`` → :class:`EventIngestor.ingest_batch`
- ``heartbeat`` → bump ``agents.last_seen_at``
- ``command_ack`` → :meth:`CommandDispatcher.handle_ack`
- ``command_result`` → :meth:`CommandDispatcher.handle_result`
- ``registry_delta`` → log only in B4 (full handling in B5)
- anything else → log + ignore

The router is created per-connection so it can hold a reference to
the connection's authenticated ``agent_id`` + ``project_id`` -
agents cannot inject events claiming to belong to a different
project.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from z4j_core.transport.frames import (
    CommandAckFrame,
    CommandResultFrame,
    EventBatchFrame,
    Frame,
    HeartbeatFrame,
    RegistryDeltaFrame,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from z4j_brain.domain import CommandDispatcher, EventIngestor
    from z4j_brain.persistence.database import DatabaseManager
    from z4j_brain.persistence.repositories import (
        AgentRepository,
        AuditLogRepository,
        CommandRepository,
        EventRepository,
        QueueRepository,
        TaskRepository,
    )
    from z4j_brain.websocket.dashboard_hub import DashboardHub


logger = structlog.get_logger("z4j.brain.frame_router")


class FrameRouter:
    """Per-connection inbound-frame dispatcher."""

    def __init__(
        self,
        *,
        db: DatabaseManager,
        ingestor: EventIngestor,
        dispatcher: CommandDispatcher,
        project_id: UUID,
        agent_id: UUID,
        dashboard_hub: "DashboardHub | None" = None,
    ) -> None:
        self._db = db
        self._ingestor = ingestor
        self._dispatcher = dispatcher
        self._project_id = project_id
        self._agent_id = agent_id
        self._dashboard_hub = dashboard_hub

    async def dispatch(self, frame: Frame) -> None:
        """Route ``frame`` to the right service. Never raises."""
        try:
            if isinstance(frame, EventBatchFrame):
                await self._handle_event_batch(frame)
            elif isinstance(frame, HeartbeatFrame):
                await self._handle_heartbeat(frame)
            elif isinstance(frame, CommandAckFrame):
                await self._handle_command_ack(frame)
            elif isinstance(frame, CommandResultFrame):
                await self._handle_command_result(frame)
            elif isinstance(frame, RegistryDeltaFrame):
                # B5 wires this into the task discovery pipeline.
                logger.debug(
                    "z4j frame_router: registry_delta received (logged-only in B4)",
                    agent_id=str(self._agent_id),
                )
            else:
                logger.warning(
                    "z4j frame_router: unhandled frame type",
                    frame_type=getattr(frame, "type", None),
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "z4j frame_router: dispatch crashed; connection survives",
                frame_type=getattr(frame, "type", None),
                agent_id=str(self._agent_id),
                project_id=str(self._project_id),
                error_class=type(exc).__name__,
            )

    # ------------------------------------------------------------------
    # event_batch
    # ------------------------------------------------------------------

    async def _handle_event_batch(self, frame: EventBatchFrame) -> None:
        # The agent's frame.payload.events list is exactly what
        # EventIngestor expects - a list of dicts with engine /
        # kind / task_id / occurred_at / data fields.
        from z4j_brain.persistence.repositories import (
            AgentRepository,
            EventRepository,
            QueueRepository,
            TaskRepository,
            WorkerRepository,
        )

        async with self._db.session() as session:
            await self._ingestor.ingest_batch(
                events=[e for e in frame.payload.events],
                project_id=self._project_id,
                agent_id=self._agent_id,
                agents=AgentRepository(session),
                event_repo=EventRepository(session),
                task_repo=TaskRepository(session),
                queue_repo=QueueRepository(session),
                worker_repo=WorkerRepository(session),
            )
            await session.commit()

        # One publish per batch (not per event) - the dashboard
        # refetches the list and gets every change in one round
        # trip. The publish runs after the commit so subscribers
        # never see a topic referencing data still in flight.
        await self._publish_task_change()

        # Evaluate per-user notification subscriptions for task-related
        # triggers. Each event may match one or more user subscriptions
        # (in-app, Slack, email, ...). We run this AFTER the commit so
        # the delivery log and any side-effect queries see the
        # committed data.
        await self._evaluate_notifications(frame.payload.events)

    # ------------------------------------------------------------------
    # heartbeat
    # ------------------------------------------------------------------

    async def _handle_heartbeat(self, frame: HeartbeatFrame) -> None:
        from z4j_brain.persistence.repositories import (
            AgentRepository,
            QueueRepository,
        )

        async with self._db.session() as session:
            await AgentRepository(session).touch_heartbeat(self._agent_id)

            # Project queue depths from the heartbeat's adapter_health.
            # The agent sends keys like "celery.queue_depths" with
            # a dict of {queue_name: depth}.
            adapter_health = frame.payload.adapter_health or {}
            for key, value in adapter_health.items():
                if key.endswith(".queue_depths") and isinstance(value, str):
                    try:
                        import json as _json

                        depths = _json.loads(value)
                        if isinstance(depths, dict):
                            queue_repo = QueueRepository(session)
                            for queue_name, depth in depths.items():
                                engine_name = key.split(".")[0]
                                q_depth = int(depth)
                                # Savepoint per queue so one bad row
                                # doesn't poison the outer tx.
                                try:
                                    async with session.begin_nested():
                                        await queue_repo.update_depth(
                                            project_id=self._project_id,
                                            engine=engine_name,
                                            name=str(queue_name),
                                            pending_count=q_depth,
                                        )
                                except Exception:  # noqa: BLE001
                                    logger.debug(
                                        "z4j frame_router: queue depth update failed",
                                        queue=str(queue_name),
                                    )
                                    continue
                                # Prometheus gauge. Best-effort:
                                # a metric-registry glitch must not
                                # break the heartbeat-ingest path.
                                try:
                                    from z4j_brain.api.metrics import z4j_queue_depth

                                    z4j_queue_depth.labels(
                                        project=str(self._project_id),
                                        queue=str(queue_name),
                                        engine=engine_name,
                                    ).set(q_depth)
                                except Exception:  # noqa: BLE001
                                    from z4j_brain.api.metrics import (
                                        record_swallowed,
                                    )

                                    record_swallowed(
                                        "frame_router", "queue_depth_gauge",
                                    )
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "z4j frame_router: failed to parse queue depths",
                            key=key,
                        )

            # Project worker details from control.inspect() data.
            # The agent sends "celery.worker_details" with a JSON
            # string of {hostname: {stats: {...}, active: [...], ...}}.
            for key, value in adapter_health.items():
                if key.endswith(".worker_details") and isinstance(value, str):
                    try:
                        import json as _json

                        from z4j_brain.persistence.enums import WorkerState
                        from z4j_brain.persistence.repositories import (
                            QueueRepository,
                            WorkerRepository,
                        )

                        details = _json.loads(value)
                        if isinstance(details, dict):
                            engine = key.split(".")[0]
                            worker_repo = WorkerRepository(session)
                            queue_repo_w = QueueRepository(session)
                            for hostname, data in details.items():
                                if not isinstance(data, dict):
                                    continue
                                stats = data.get("stats", {})
                                if isinstance(stats, str):
                                    stats = _json.loads(stats)
                                pool = stats.get("pool", {}) if isinstance(stats, dict) else {}
                                rusage = stats.get("rusage", {}) if isinstance(stats, dict) else {}
                                total = stats.get("total", {}) if isinstance(stats, dict) else {}

                                updates: dict[str, Any] = {
                                    "state": WorkerState.ONLINE,
                                    "last_heartbeat": frame.payload.last_flush_at or datetime.now(UTC),
                                    "hostname": hostname,
                                    "worker_metadata": {
                                        "stats": stats,
                                        "active": data.get("active", []),
                                        "active_queues": data.get("active_queues", []),
                                        "registered": data.get("registered", []),
                                        "conf": data.get("conf", {}),
                                    },
                                }
                                # Pool info
                                if isinstance(pool, dict):
                                    updates["concurrency"] = pool.get(
                                        "max-concurrency",
                                        pool.get("processes", None),
                                    )
                                    updates["pid"] = stats.get("pid")
                                # Active tasks
                                active = data.get("active", [])
                                if isinstance(active, list):
                                    updates["active_tasks"] = len(active)
                                # Active queues
                                aq = data.get("active_queues", [])
                                if isinstance(aq, list):
                                    updates["queues"] = [
                                        q.get("name", "") for q in aq
                                        if isinstance(q, dict)
                                    ]
                                # Load average
                                if isinstance(rusage, dict):
                                    loadavg = stats.get("loadavg")
                                    if isinstance(loadavg, list):
                                        updates["load_average"] = loadavg

                                # Savepoint per worker upsert. Without
                                # this, a concurrent-heartbeat deadlock
                                # between two frontends both touching
                                # the same (workers / queues) FK graph
                                # bubbles ``DeadlockDetectedError`` out
                                # of this statement, poisons the outer
                                # transaction, and blows up the later
                                # ``session.commit()`` with
                                # ``PendingRollbackError`` - which in
                                # turn drops ``touch_heartbeat`` (the
                                # write that updates ``last_seen_at``
                                # and keeps the agent state=online).
                                # Audit pass 9 (2026-04-21) reproduced
                                # this with 3 FastAPI workers ×
                                # prefork=2 ≈ 6 concurrent heartbeats
                                # every 10s.
                                #
                                # On deadlock we log+skip this worker's
                                # detail for this heartbeat cycle; the
                                # next heartbeat in 10s retries and
                                # usually succeeds. Queue depth updates
                                # below are already savepoint-wrapped;
                                # this brings the worker path in line.
                                try:
                                    async with session.begin_nested():
                                        await worker_repo.upsert_from_event(
                                            project_id=self._project_id,
                                            engine=engine,
                                            name=hostname,
                                            updates=updates,
                                        )
                                except Exception:  # noqa: BLE001
                                    logger.debug(
                                        "z4j frame_router: worker upsert "
                                        "failed (likely deadlock); skipping "
                                        "this hostname for this heartbeat",
                                        engine=engine,
                                        hostname=str(hostname),
                                    )
                                    continue

                                # Register each queue this worker is
                                # consuming so the Queues page reflects
                                # them even when task events don't
                                # carry a ``queue`` field (Celery only
                                # emits queue names for explicit
                                # routing; default-queue tasks arrive
                                # with queue=None, leaving the Queues
                                # page empty otherwise).
                                queue_names = updates.get("queues") or []
                                for qname in queue_names:
                                    if not (isinstance(qname, str) and qname):
                                        continue
                                    # Each touch runs in its own
                                    # savepoint. Without this a
                                    # single bad queue name poisons
                                    # the outer session on Postgres
                                    # (``InFailedSqlTransactionError``)
                                    # and silently rolls back the
                                    # worker state + heartbeats we
                                    # just wrote.
                                    try:
                                        async with session.begin_nested():
                                            await queue_repo_w.touch(
                                                project_id=self._project_id,
                                                engine=engine,
                                                name=qname,
                                            )
                                    except Exception:  # noqa: BLE001
                                        logger.exception(
                                            "z4j frame_router: queue touch failed",
                                        )
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "z4j frame_router: failed to parse worker details",
                        )

            await session.commit()

    # ------------------------------------------------------------------
    # command_ack / command_result
    # ------------------------------------------------------------------

    async def _handle_command_ack(self, frame: CommandAckFrame) -> None:
        from uuid import UUID as _UUID

        try:
            command_id = _UUID(frame.id)
        except ValueError:
            return
        from z4j_brain.persistence.repositories import CommandRepository

        async with self._db.session() as session:
            await self._dispatcher.handle_ack(
                commands=CommandRepository(session),
                command_id=command_id,
            )
            await session.commit()

        await self._publish_command_change()

    async def _handle_command_result(self, frame: CommandResultFrame) -> None:
        from uuid import UUID as _UUID

        try:
            command_id = _UUID(frame.id)
        except ValueError:
            return
        from z4j_brain.persistence.repositories import (
            AuditLogRepository,
            CommandRepository,
        )

        async with self._db.session() as session:
            await self._dispatcher.handle_result(
                commands=CommandRepository(session),
                audit_log=AuditLogRepository(session),
                command_id=command_id,
                status=frame.payload.status,
                result_payload=frame.payload.result,
                error=frame.payload.error,
            )
            await session.commit()

        await self._publish_command_change()

    # ------------------------------------------------------------------
    # Dashboard publish helpers
    # ------------------------------------------------------------------

    async def _evaluate_notifications(
        self,
        events: list[dict[str, Any]],
    ) -> None:
        """Fire per-user notification subscriptions for task state changes."""
        from z4j_core.models.event import EventKind

        from z4j_brain.domain.notifications import NotificationService

        # Map event kinds to notification trigger types.
        KIND_TO_TRIGGER: dict[str, str] = {
            EventKind.TASK_FAILED.value: "task.failed",
            EventKind.TASK_SUCCEEDED.value: "task.succeeded",
            EventKind.TASK_RETRIED.value: "task.retried",
        }

        # Deduplicate: only fire once per (trigger, task_id) per batch.
        seen: set[tuple[str, str]] = set()

        for raw_event in events:
            kind = raw_event.get("kind", "")
            trigger = KIND_TO_TRIGGER.get(kind)
            if trigger is None:
                continue
            task_id = raw_event.get("task_id", "")
            if (trigger, task_id) in seen:
                continue
            seen.add((trigger, task_id))

            data = raw_event.get("data") or {}
            try:
                async with self._db.session() as session:
                    svc = NotificationService()
                    await svc.evaluate_and_dispatch(
                        session=session,
                        project_id=self._project_id,
                        trigger=trigger,
                        task_id=task_id,
                        task_name=data.get("task_name"),
                        # Forward the engine name onto the notification
                        # payload so the dashboard can deep-link to
                        # /tasks/<engine>/<task_id> for RQ + Dramatiq
                        # tasks (fixes the BUG-2 celery-fallback 404).
                        engine=raw_event.get("engine"),
                        priority=data.get("priority", "normal"),
                        state=kind.split(".")[-1] if "." in kind else kind,
                        queue=data.get("queue"),
                        exception=data.get("exception"),
                        traceback=data.get("traceback"),
                    )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "z4j frame_router: notification evaluation failed",
                    trigger=trigger,
                    task_id=task_id,
                )

    async def _publish_task_change(self) -> None:
        if self._dashboard_hub is None:
            return
        try:
            await self._dashboard_hub.publish_task_change(self._project_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "z4j frame_router: dashboard task publish failed",
                project_id=str(self._project_id),
            )

    async def _publish_command_change(self) -> None:
        if self._dashboard_hub is None:
            return
        try:
            await self._dashboard_hub.publish_command_change(self._project_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "z4j frame_router: dashboard command publish failed",
                project_id=str(self._project_id),
            )


__all__ = ["FrameRouter"]
