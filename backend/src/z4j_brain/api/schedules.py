"""``/api/v1/projects/{slug}/schedules`` REST router.

Provides:

- ``GET    /``                - list schedules in the project
- ``GET    /{schedule_id}``    - schedule detail
- ``POST   /{schedule_id}/enable``  - enable (issues schedule.enable command)
- ``POST   /{schedule_id}/disable`` - disable
- ``POST   /{schedule_id}/trigger`` - fire-now (issues schedule.trigger_now)

Schedules CRUD (create / update / delete via the brain) lands in
B6 alongside the registry-delta sync - for now schedules are
created in the user's own celery-beat / APScheduler and the brain
mirrors them via the agent's signal hooks.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from z4j_brain.api.deps import (
    get_audit_log_repo,
    get_audit_service,
    get_client_ip,
    get_command_dispatcher,
    get_current_user,
    get_membership_repo,
    get_project_repo,
    get_session,
    get_settings,
    require_csrf,
)
from z4j_brain.domain.ip_rate_limit import require_bulk_action_throttle
from z4j_brain.errors import NotFoundError
from z4j_brain.persistence.enums import ProjectRole

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from z4j_brain.domain.audit_service import AuditService
    from z4j_brain.domain.command_dispatcher import CommandDispatcher
    from z4j_brain.persistence.models import Agent, Schedule, User
    from z4j_brain.persistence.repositories import (
        AuditLogRepository,
        MembershipRepository,
        ProjectRepository,
    )
    from z4j_brain.settings import Settings


router = APIRouter(prefix="/projects/{slug}/schedules", tags=["schedules"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SchedulePublic(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    engine: str
    scheduler: str
    name: str
    task_name: str
    kind: str
    expression: str
    timezone: str
    queue: str | None
    priority: str
    args: Any
    kwargs: Any
    is_enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    total_runs: int
    external_id: str | None
    created_at: datetime
    updated_at: datetime


def _payload(schedule: "Schedule") -> SchedulePublic:
    return SchedulePublic(
        id=schedule.id,
        project_id=schedule.project_id,
        engine=schedule.engine,
        scheduler=schedule.scheduler,
        name=schedule.name,
        task_name=schedule.task_name,
        kind=schedule.kind.value,
        expression=schedule.expression,
        timezone=schedule.timezone,
        queue=schedule.queue,
        priority=schedule.priority.value if hasattr(schedule.priority, "value") else str(schedule.priority or "normal"),
        args=schedule.args,
        kwargs=schedule.kwargs,
        is_enabled=schedule.is_enabled,
        last_run_at=schedule.last_run_at,
        next_run_at=schedule.next_run_at,
        total_runs=schedule.total_runs,
        external_id=schedule.external_id,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[SchedulePublic])
async def list_schedules(
    slug: str,
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
    db_session: "AsyncSession" = Depends(get_session),
) -> list[SchedulePublic]:
    from z4j_brain.domain.policy_engine import PolicyEngine
    from z4j_brain.persistence.repositories import ScheduleRepository

    policy = PolicyEngine()
    project = await policy.get_project_or_404(projects, slug)
    await policy.require_member(
        memberships,
        user=user,
        project_id=project.id,
        min_role=ProjectRole.VIEWER,
    )
    rows = await ScheduleRepository(db_session).list_for_project(project.id)
    return [_payload(s) for s in rows]


@router.get("/{schedule_id}", response_model=SchedulePublic)
async def get_schedule(
    slug: str,
    schedule_id: uuid.UUID,
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
    db_session: "AsyncSession" = Depends(get_session),
) -> SchedulePublic:
    from z4j_brain.domain.policy_engine import PolicyEngine
    from z4j_brain.persistence.repositories import ScheduleRepository

    policy = PolicyEngine()
    project = await policy.get_project_or_404(projects, slug)
    await policy.require_member(
        memberships,
        user=user,
        project_id=project.id,
        min_role=ProjectRole.VIEWER,
    )
    schedule = await ScheduleRepository(db_session).get_for_project(
        project_id=project.id, schedule_id=schedule_id,
    )
    if schedule is None:
        raise NotFoundError(
            "schedule not found",
            details={"schedule_id": str(schedule_id)},
        )
    return _payload(schedule)


# ---------------------------------------------------------------------------
# Write endpoints - issue commands to the agent
# ---------------------------------------------------------------------------


async def _pick_scheduler_agent(
    *,
    db_session: "AsyncSession",
    project_id: uuid.UUID,
    scheduler_name: str,
    schedule_id: uuid.UUID,
) -> "Agent":
    """Pick the best online agent to receive a schedule command.

    Filters the project's agents down to those that are:

    1. Currently online (``state == ONLINE``), and
    2. Registered for the schedule's specific scheduler adapter
       (``scheduler_name`` in ``agent.scheduler_adapters``).

    Returns the freshest match (``list_online_for_project`` orders
    by ``last_seen_at DESC``). Raises :class:`NotFoundError` with a
    helpful message if nothing matches so the dashboard can surface
    the root cause (offline agent vs. adapter not installed) rather
    than the old "no agent registered" generic string.

    Audit 2026-04-24 Medium-4: before this helper the caller did
    ``next(iter(list_for_project(...)), None)``, which picked ANY
    agent regardless of online state or scheduler support - so a
    Django+Celery+RQ project could send ``schedule.enable`` to the
    RQ-only agent and leave the brain's optimistic ``is_enabled``
    out of sync forever.
    """
    from z4j_brain.persistence.repositories import AgentRepository

    agents = await AgentRepository(db_session).list_online_for_project(
        project_id,
    )
    for agent in agents:
        if scheduler_name in (agent.scheduler_adapters or ()):
            return agent

    # None matched. Tell the caller WHY so the UI can help the
    # operator: is no agent online at all, or is one online but
    # without this scheduler adapter?
    if not agents:
        raise NotFoundError(
            "no online agent for this project; start the agent "
            "and retry",
            details={
                "schedule_id": str(schedule_id),
                "scheduler": scheduler_name,
                "reason": "no_online_agent",
            },
        )
    raise NotFoundError(
        f"no online agent advertises scheduler {scheduler_name!r}; "
        f"install the matching scheduler adapter on an agent and "
        f"restart it",
        details={
            "schedule_id": str(schedule_id),
            "scheduler": scheduler_name,
            "reason": "scheduler_not_installed",
        },
    )


async def _enable_or_disable(
    *,
    slug: str,
    schedule_id: uuid.UUID,
    enabled: bool,
    user: "User",
    memberships: "MembershipRepository",
    projects: "ProjectRepository",
    audit_log: "AuditLogRepository",
    dispatcher: "CommandDispatcher",
    db_session: "AsyncSession",
    ip: str,
) -> SchedulePublic:
    from z4j_brain.domain.policy_engine import PolicyEngine
    from z4j_brain.persistence.repositories import (
        CommandRepository,
        ScheduleRepository,
    )

    policy = PolicyEngine()
    project = await policy.get_project_or_404(projects, slug)
    await policy.require_member(
        memberships,
        user=user,
        project_id=project.id,
        min_role=ProjectRole.OPERATOR,
    )

    schedules_repo = ScheduleRepository(db_session)
    schedule = await schedules_repo.get_for_project(
        project_id=project.id, schedule_id=schedule_id,
    )
    if schedule is None:
        raise NotFoundError(
            "schedule not found",
            details={"schedule_id": str(schedule_id)},
        )

    target_agent = await _pick_scheduler_agent(
        db_session=db_session,
        project_id=project.id,
        scheduler_name=schedule.scheduler,
        schedule_id=schedule_id,
    )

    action = "schedule.enable" if enabled else "schedule.disable"
    commands = CommandRepository(db_session)
    await dispatcher.issue(
        commands=commands,
        audit_log=audit_log,
        project_id=project.id,
        agent_id=target_agent.id,
        action=action,
        target_type="schedule",
        target_id=str(schedule_id),
        payload={
            "schedule_id": schedule.name,
            "schedule_name": schedule.name,
            "external_id": schedule.external_id,
        },
        issued_by=user.id,
        ip=ip,
        user_agent=None,
    )
    # Optimistically reflect the operator's intent in the brain row.
    # The agent will sync back the authoritative state on success.
    await schedules_repo.set_enabled(
        schedule_id=schedule_id, enabled=enabled,
    )
    await db_session.commit()

    await dispatcher.notify_dashboard_command_change(project.id)

    refreshed = await schedules_repo.get_for_project(
        project_id=project.id, schedule_id=schedule_id,
    )
    assert refreshed is not None
    return _payload(refreshed)


@router.post(
    "/{schedule_id}/enable",
    response_model=SchedulePublic,
    dependencies=[Depends(require_csrf)],
)
async def enable_schedule(
    slug: str,
    schedule_id: uuid.UUID,
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
    audit_log: "AuditLogRepository" = Depends(get_audit_log_repo),
    dispatcher: "CommandDispatcher" = Depends(get_command_dispatcher),
    db_session: "AsyncSession" = Depends(get_session),
    ip: str = Depends(get_client_ip),
) -> SchedulePublic:
    return await _enable_or_disable(
        slug=slug,
        schedule_id=schedule_id,
        enabled=True,
        user=user,
        memberships=memberships,
        projects=projects,
        audit_log=audit_log,
        dispatcher=dispatcher,
        db_session=db_session,
        ip=ip,
    )


@router.post(
    "/{schedule_id}/disable",
    response_model=SchedulePublic,
    dependencies=[Depends(require_csrf)],
)
async def disable_schedule(
    slug: str,
    schedule_id: uuid.UUID,
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
    audit_log: "AuditLogRepository" = Depends(get_audit_log_repo),
    dispatcher: "CommandDispatcher" = Depends(get_command_dispatcher),
    db_session: "AsyncSession" = Depends(get_session),
    ip: str = Depends(get_client_ip),
) -> SchedulePublic:
    return await _enable_or_disable(
        slug=slug,
        schedule_id=schedule_id,
        enabled=False,
        user=user,
        memberships=memberships,
        projects=projects,
        audit_log=audit_log,
        dispatcher=dispatcher,
        db_session=db_session,
        ip=ip,
    )


@router.post(
    "/{schedule_id}/trigger",
    response_model=SchedulePublic,
    dependencies=[
        Depends(require_csrf),
        Depends(require_bulk_action_throttle),
    ],
)
async def trigger_schedule_now(
    slug: str,
    schedule_id: uuid.UUID,
    request: "Request",
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
    audit_log: "AuditLogRepository" = Depends(get_audit_log_repo),
    audit: "AuditService" = Depends(get_audit_service),
    dispatcher: "CommandDispatcher" = Depends(get_command_dispatcher),
    settings: "Settings" = Depends(get_settings),
    db_session: "AsyncSession" = Depends(get_session),
    ip: str = Depends(get_client_ip),
) -> SchedulePublic:
    """Issue a one-shot ``schedule.trigger_now`` command.

    The schedule itself is unchanged - its normal cadence is
    untouched. The agent fires the underlying task once.
    """
    from z4j_brain.domain.policy_engine import PolicyEngine
    from z4j_brain.persistence.repositories import (
        CommandRepository,
        ScheduleRepository,
    )

    policy = PolicyEngine()
    project = await policy.get_project_or_404(projects, slug)
    await policy.require_member(
        memberships,
        user=user,
        project_id=project.id,
        min_role=ProjectRole.OPERATOR,
    )

    schedules_repo = ScheduleRepository(db_session)
    schedule = await schedules_repo.get_for_project(
        project_id=project.id, schedule_id=schedule_id,
    )
    if schedule is None:
        raise NotFoundError(
            "schedule not found",
            details={"schedule_id": str(schedule_id)},
        )

    # Phase 2: when the schedule is owned by z4j-scheduler AND the
    # operator has wired the trigger client, route the trigger
    # through the scheduler so its local cache last_fire_at gets
    # the update (preventing the next tick from double-firing).
    # Falls through to the v1 direct-dispatch path for any
    # combination that doesn't match.
    # Phase 2: when the schedule is owned by z4j-scheduler AND the
    # operator has wired the trigger client, route through the
    # scheduler so its local cache last_fire_at gets the update
    # (preventing the next tick from double-firing).
    use_scheduler_grpc = (
        schedule.scheduler == "z4j-scheduler"
        and bool(settings.scheduler_trigger_url)
    )
    if use_scheduler_grpc:
        client = await _get_or_build_trigger_client(request, settings)
        response = await client.trigger(
            schedule_id=schedule_id,
            user_id=user.id,
            idempotency_key=f"trigger:{schedule_id}:{user.id}",
        )
        if response.error_code:
            raise NotFoundError(
                f"scheduler rejected trigger: {response.error_code}",
                details={
                    "error_code": response.error_code,
                    "error_message": response.error_message,
                },
            )
        # Audit the trigger on the brain side (the scheduler audits
        # its dispatch separately). One row per click is the
        # operator-facing record.
        await audit.record(
            audit_log,
            action="schedule.trigger_now.via_scheduler",
            target_type="schedule",
            target_id=str(schedule_id),
            result="success",
            outcome="allow",
            user_id=user.id,
            project_id=project.id,
            source_ip=ip,
            metadata={
                "scheduler_command_id": response.command_id,
                "scheduler_url": settings.scheduler_trigger_url,
            },
        )
        await db_session.commit()
        refreshed = await schedules_repo.get_for_project(
            project_id=project.id, schedule_id=schedule_id,
        )
        assert refreshed is not None
        return _payload(refreshed)

    # v1 direct-dispatch path: pick a matching agent and issue the
    # schedule.trigger_now command. Used when no scheduler is
    # attached, or when the schedule is owned by celery-beat /
    # apscheduler / etc. on the agent side.
    target_agent = await _pick_scheduler_agent(
        db_session=db_session,
        project_id=project.id,
        scheduler_name=schedule.scheduler,
        schedule_id=schedule_id,
    )

    await dispatcher.issue(
        commands=CommandRepository(db_session),
        audit_log=audit_log,
        project_id=project.id,
        agent_id=target_agent.id,
        action="schedule.trigger_now",
        target_type="schedule",
        target_id=str(schedule_id),
        payload={
            "schedule_id": schedule.name,
            "schedule_name": schedule.name,
            "external_id": schedule.external_id,
        },
        issued_by=user.id,
        ip=ip,
        user_agent=None,
    )
    await db_session.commit()

    await dispatcher.notify_dashboard_command_change(project.id)

    refreshed = await schedules_repo.get_for_project(
        project_id=project.id, schedule_id=schedule_id,
    )
    assert refreshed is not None
    return _payload(refreshed)


# ---------------------------------------------------------------------------
# Trigger-client singleton helper
# ---------------------------------------------------------------------------


async def _get_or_build_trigger_client(
    request: "Request",
    settings: "Settings",
):
    """Return a process-wide :class:`TriggerScheduleClient` singleton.

    First call lazily constructs the client + opens the gRPC channel
    and caches it on ``app.state.scheduler_trigger_client``. Every
    subsequent trigger reuses the same channel - which means one TLS
    handshake per brain process, not one per click. The previous
    per-call construction was an audit-Phase2 finding (TLS cost on
    every trigger button push).

    The brain shutdown path closes the client in
    ``z4j_brain.main._lifespan`` finally-block.
    """
    cached = getattr(request.app.state, "scheduler_trigger_client", None)
    if cached is not None:
        return cached
    from z4j_brain.scheduler_grpc.trigger_client import (  # noqa: PLC0415
        TriggerScheduleClient,
    )

    client = TriggerScheduleClient(settings=settings)
    await client.connect()
    request.app.state.scheduler_trigger_client = client
    return client


# ---------------------------------------------------------------------------
# Bulk import (z4j-scheduler migration importers)
# ---------------------------------------------------------------------------


class ImportedScheduleIn(BaseModel):
    """One row of a bulk import payload.

    Mirrors :class:`z4j_scheduler.importers._core.ImportedSchedule` -
    the importers compute ``source_hash`` for re-import idempotency
    and carry through ``source`` so the dashboard can render a
    "managed by celery-beat (imported)" badge.

    ``project_slug`` is dropped on the wire because the URL already
    pins the project; we accept it if the importer still sends it
    (using ``model_config = ConfigDict(extra="ignore")``) but never
    use it.
    """

    name: str
    engine: str
    kind: str
    expression: str
    task_name: str
    timezone: str = "UTC"
    queue: str | None = None
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    catch_up: str = "skip"
    is_enabled: bool = True
    scheduler: str = "z4j-scheduler"
    source: str = "imported"
    source_hash: str | None = None

    # Pydantic v2 - allow extra keys (project_slug from importer
    # client) without erroring. We only consume the fields above.
    model_config = {"extra": "ignore"}


class ImportSchedulesRequest(BaseModel):
    """POST body for ``/api/v1/projects/{slug}/schedules:import``."""

    schedules: list[ImportedScheduleIn]


class ImportSchedulesResponse(BaseModel):
    """Per-batch summary returned to the importer.

    Lets the operator see at a glance whether their re-import was a
    real diff or a no-op. ``errors`` carries human-readable messages
    keyed by index in the input list so the operator can pinpoint
    which row failed without re-correlating by name.
    """

    inserted: int
    updated: int
    unchanged: int
    failed: int
    errors: dict[int, str] = {}


@router.post(
    ":import",
    response_model=ImportSchedulesResponse,
    status_code=200,
    dependencies=[Depends(require_csrf)],
)
async def import_schedules(
    slug: str,
    body: ImportSchedulesRequest,
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
    audit_log: "AuditLogRepository" = Depends(get_audit_log_repo),
    audit: "AuditService" = Depends(get_audit_service),
    db_session: "AsyncSession" = Depends(get_session),
    ip: str = Depends(get_client_ip),
) -> ImportSchedulesResponse:
    """Bulk-import schedules from a migration tool.

    Called by ``z4j-scheduler import --from <tool>``. Each row is
    upserted by ``(project_id, scheduler, name)``. Re-imports with
    matching ``source_hash`` are no-ops so the operator can re-run
    the importer without flooding the audit log.

    Authorization: project ADMIN. Schedule import is a privileged
    operation - it adds new fire surfaces to a project, which can
    move money, send emails, etc. ADMIN matches the existing
    convention for membership / retention / token mutations.
    """
    from z4j_brain.domain.policy_engine import PolicyEngine
    from z4j_brain.persistence.repositories import upsert_imported_schedule

    policy = PolicyEngine()
    project = await policy.get_project_or_404(projects, slug)
    await policy.require_member(
        memberships,
        user=user,
        project_id=project.id,
        min_role=ProjectRole.ADMIN,
    )

    summary = ImportSchedulesResponse(
        inserted=0, updated=0, unchanged=0, failed=0, errors={},
    )
    for idx, row in enumerate(body.schedules):
        try:
            outcome = await upsert_imported_schedule(
                session=db_session,
                project_id=project.id,
                data=row.model_dump(),
            )
        except ValueError as exc:
            # Per-row validation failure (bad kind, empty name, etc.)
            # - record + continue. The whole batch still commits if
            # at least one row succeeded; the operator gets the
            # error map so they can fix the source and re-import.
            summary.failed += 1
            summary.errors[idx] = str(exc)
            continue
        if outcome == "inserted":
            summary.inserted += 1
        elif outcome == "updated":
            summary.updated += 1
        else:
            summary.unchanged += 1

    # Single audit row for the whole batch. Per-row audit would
    # spam the log when the importer is run on a big celery-beat
    # config (a 50-schedule import generating 50 audit entries
    # buries everything else for that minute).
    await audit.record(
        audit_log,
        action="schedules.import",
        target_type="project",
        target_id=str(project.id),
        result="success" if summary.failed == 0 else "partial",
        outcome="allow",
        user_id=user.id,
        project_id=project.id,
        source_ip=ip,
        metadata={
            "inserted": summary.inserted,
            "updated": summary.updated,
            "unchanged": summary.unchanged,
            "failed": summary.failed,
        },
    )
    await db_session.commit()
    return summary


__all__ = [
    "ImportSchedulesRequest",
    "ImportSchedulesResponse",
    "ImportedScheduleIn",
    "SchedulePublic",
    "router",
]
