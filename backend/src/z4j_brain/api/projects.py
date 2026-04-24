"""``/api/v1/projects`` REST router.

Read endpoints scope to membership; write endpoints
(create / update / archive) require global brain admin.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import update

from z4j_brain.api.deps import (
    get_audit_log_repo,
    get_audit_service,
    get_client_ip,
    get_current_user,
    get_membership_repo,
    get_project_repo,
    get_session,
    get_settings,
    require_admin,
    require_csrf,
)
from z4j_brain.errors import ConflictError, NotFoundError
from z4j_brain.persistence.enums import ProjectRole

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from z4j_brain.domain.audit_service import AuditService
    from z4j_brain.persistence.models import Project, User
    from z4j_brain.persistence.repositories import (
        AuditLogRepository,
        MembershipRepository,
        ProjectRepository,
    )
    from z4j_brain.settings import Settings


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$")


router = APIRouter(prefix="/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ProjectPublic(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    environment: str
    timezone: str
    retention_days: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CreateProjectRequest(BaseModel):
    slug: str = Field(min_length=2, max_length=63)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    environment: str = Field(default="production", max_length=40)
    timezone: str = Field(default="UTC", max_length=64)
    retention_days: int = Field(default=30, ge=1, le=3650)


class UpdateProjectRequest(BaseModel):
    slug: str | None = Field(default=None, min_length=2, max_length=63)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    environment: str | None = Field(default=None, max_length=40)
    timezone: str | None = Field(default=None, max_length=64)
    retention_days: int | None = Field(default=None, ge=1, le=3650)


def _project_payload(project: "Project") -> ProjectPublic:
    return ProjectPublic(
        id=project.id,
        slug=project.slug,
        name=project.name,
        description=project.description,
        environment=project.environment,
        timezone=project.timezone,
        retention_days=project.retention_days,
        is_active=project.is_active,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


# ---------------------------------------------------------------------------
# Read routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ProjectPublic])
async def list_projects(
    request: Request,
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
    settings: "Settings" = Depends(get_settings),
) -> list[ProjectPublic]:
    """List the projects the current user has access to.

    When the caller is authenticated via a **project-scoped Bearer
    key**, the response is further filtered to only that bound
    project - the scope layer writes
    ``request.state.api_key_project_slug`` at auth time and this
    handler respects it. Without this filter a project-A-bound
    key could enumerate every project the owner user can see
    (external audit, Critical #1).
    """
    bound_slug: str | None = getattr(
        request.state, "api_key_project_slug", None,
    )

    if user.is_admin:
        rows = await projects.list(limit=settings.admin_project_list_cap, offset=0)
        payload = [_project_payload(p) for p in rows if p.is_active]
    else:
        member_rows = await memberships.list_for_user(user.id)
        project_ids = {m.project_id for m in member_rows}
        if not project_ids:
            return []
        rows = await projects.list_by_ids(project_ids, only_active=True)
        payload = [_project_payload(p) for p in rows]

    if bound_slug is not None:
        payload = [p for p in payload if p.slug == bound_slug]
    return payload


@router.get("/{slug}", response_model=ProjectPublic)
async def get_project(
    slug: str,
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
) -> ProjectPublic:
    from z4j_brain.domain.policy_engine import PolicyEngine

    policy = PolicyEngine()
    project = await policy.get_project_or_404(projects, slug)
    await policy.require_member(
        memberships,
        user=user,
        project_id=project.id,
        min_role=ProjectRole.VIEWER,
    )
    return _project_payload(project)


# ---------------------------------------------------------------------------
# Write routes - global brain admin only
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=ProjectPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_project(
    body: CreateProjectRequest,
    admin: "User" = Depends(require_admin),
    projects: "ProjectRepository" = Depends(get_project_repo),
    audit: "AuditService" = Depends(get_audit_service),
    audit_log: "AuditLogRepository" = Depends(get_audit_log_repo),
    db_session: "AsyncSession" = Depends(get_session),
    ip: str = Depends(get_client_ip),
) -> ProjectPublic:
    if not _SLUG_RE.match(body.slug):
        raise ConflictError(
            "slug must match ^[a-z0-9][a-z0-9-]{1,62}$",
            details={"slug": body.slug},
        )
    existing = await projects.get_by_slug(body.slug)
    if existing is not None:
        raise ConflictError(
            "project slug already in use",
            details={"slug": body.slug},
        )

    from z4j_brain.persistence.models import Project

    project = Project(
        slug=body.slug,
        name=body.name,
        description=body.description,
        environment=body.environment,
        timezone=body.timezone,
        retention_days=body.retention_days,
    )
    await projects.add(project)

    await audit.record(
        audit_log,
        action="project.created",
        target_type="project",
        target_id=str(project.id),
        result="success",
        outcome="allow",
        user_id=admin.id,
        project_id=project.id,
        source_ip=ip,
        metadata={"slug": body.slug},
    )
    await db_session.commit()
    return _project_payload(project)


@router.patch(
    "/{slug}",
    response_model=ProjectPublic,
    dependencies=[Depends(require_csrf)],
)
async def update_project(
    slug: str,
    body: UpdateProjectRequest,
    admin: "User" = Depends(require_admin),
    projects: "ProjectRepository" = Depends(get_project_repo),
    audit: "AuditService" = Depends(get_audit_service),
    audit_log: "AuditLogRepository" = Depends(get_audit_log_repo),
    db_session: "AsyncSession" = Depends(get_session),
    ip: str = Depends(get_client_ip),
) -> ProjectPublic:
    project = await projects.get_by_slug(slug)
    if project is None:
        raise NotFoundError(
            "project not found",
            details={"slug": slug},
        )
    changed: dict[str, object] = {}
    # Slug is the project's external identifier. Renaming is allowed
    # but the new slug must match the same format and not collide
    # with an existing project. Callers that bookmark the old URL
    # will get a 404 until they refresh - that's the accepted
    # trade-off of exposing slugs in the API surface.
    if body.slug is not None and body.slug != project.slug:
        if not _SLUG_RE.match(body.slug):
            raise ConflictError(
                "slug must match ^[a-z0-9][a-z0-9-]{1,62}$",
                details={"slug": body.slug},
            )
        existing = await projects.get_by_slug(body.slug)
        if existing is not None and existing.id != project.id:
            raise ConflictError(
                "project slug already in use",
                details={"slug": body.slug},
            )
        changed["slug"] = body.slug
        project.slug = body.slug
    for field in (
        "name", "description", "environment", "timezone", "retention_days",
    ):
        value = getattr(body, field, None)
        if value is not None:
            changed[field] = value
            setattr(project, field, value)
    project.updated_at = datetime.now(UTC)

    if changed:
        await audit.record(
            audit_log,
            action="project.updated",
            target_type="project",
            target_id=str(project.id),
            result="success",
            outcome="allow",
            user_id=admin.id,
            project_id=project.id,
            source_ip=ip,
            metadata={"changed": list(changed.keys())},
        )
    await db_session.commit()
    return _project_payload(project)


@router.delete(
    "/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def archive_project(
    slug: str,
    admin: "User" = Depends(require_admin),
    projects: "ProjectRepository" = Depends(get_project_repo),
    audit: "AuditService" = Depends(get_audit_service),
    audit_log: "AuditLogRepository" = Depends(get_audit_log_repo),
    db_session: "AsyncSession" = Depends(get_session),
    ip: str = Depends(get_client_ip),
) -> None:
    """Soft-archive a project (sets ``is_active=False``).

    We never hard-delete projects - that would cascade across the
    audit log and break the historical record. The archived
    project is hidden from list views but its rows survive.
    """
    project = await projects.get_by_slug(slug)
    if project is None:
        raise NotFoundError(
            "project not found",
            details={"slug": slug},
        )
    if not project.is_active:
        return  # idempotent

    active_count = await projects.count_active()
    if active_count <= 1:
        raise ConflictError(
            "cannot archive the last remaining project",
            details={"slug": slug},
        )

    project.is_active = False
    project.updated_at = datetime.now(UTC)
    await audit.record(
        audit_log,
        action="project.archived",
        target_type="project",
        target_id=str(project.id),
        result="success",
        outcome="allow",
        user_id=admin.id,
        project_id=project.id,
        source_ip=ip,
    )
    await db_session.commit()


__all__ = [
    "CreateProjectRequest",
    "ProjectPublic",
    "UpdateProjectRequest",
    "router",
]
