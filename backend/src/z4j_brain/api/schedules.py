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

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from z4j_brain.api.deps import (
    get_audit_log_repo,
    get_client_ip,
    get_command_dispatcher,
    get_current_user,
    get_membership_repo,
    get_project_repo,
    get_session,
    require_csrf,
)
from z4j_brain.domain.ip_rate_limit import require_bulk_action_throttle
from z4j_brain.errors import NotFoundError
from z4j_brain.persistence.enums import ProjectRole

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from z4j_brain.domain.command_dispatcher import CommandDispatcher
    from z4j_brain.persistence.models import Agent, Schedule, User
    from z4j_brain.persistence.repositories import (
        AuditLogRepository,
        MembershipRepository,
        ProjectRepository,
    )


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
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
    audit_log: "AuditLogRepository" = Depends(get_audit_log_repo),
    dispatcher: "CommandDispatcher" = Depends(get_command_dispatcher),
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


__all__ = ["SchedulePublic", "router"]
