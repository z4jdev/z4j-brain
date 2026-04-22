"""Project-scoped notification endpoints.

Three sub-resources, all under ``/api/v1/projects/{slug}/notifications``:

- ``/channels``  - shared channels project admins manage. Any
  member can reference these in their personal subscriptions.
  Admin-only for write, member-readable for select-list use.

- ``/defaults``  - project default subscriptions. Admin-managed
  templates that materialize into ``user_subscriptions`` whenever
  a user joins the project.

- ``/deliveries`` - audit log of external delivery attempts.
  Admin-only.

Per-user resources live in :mod:`z4j_brain.api.user_notifications`
under the ``/api/v1/user/`` prefix.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from z4j_brain.api.deps import (
    get_current_user,
    get_membership_repo,
    get_project_repo,
    get_session,
    require_csrf,
)
from z4j_brain.domain.notifications.channels import (
    validate_webhook_headers,
    validate_webhook_url,
)
from z4j_brain.errors import ConflictError
from z4j_brain.persistence.enums import ProjectRole

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from z4j_brain.persistence.models import User
    from z4j_brain.persistence.repositories import (
        MembershipRepository,
        ProjectRepository,
    )


router = APIRouter(
    prefix="/projects/{slug}/notifications",
    tags=["notifications"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


_TRIGGER_PATTERN = (
    r"^(task\.failed|task\.succeeded|task\.retried|"
    r"task\.slow|agent\.offline|agent\.online)$"
)
_CHANNEL_TYPE_PATTERN = r"^(webhook|email|slack|telegram)$"


class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(pattern=_CHANNEL_TYPE_PATTERN)
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, Any] | None = None
    is_active: bool | None = None


class ChannelPublic(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    type: str
    config: dict[str, Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SubscriptionFilters(BaseModel):
    """Strict shape for subscription filter JSON.

    HIGH-06: the service's ``_matches_filters`` expects typed fields
    (e.g. ``priority`` is a list of strings). Without this model the
    API accepted arbitrary shapes and silently dropped mistyped
    filters, so users would see unfiltered floods of notifications.
    ``extra=forbid`` rejects unknown keys to surface typos early.
    ``task_name_pattern`` is capped to prevent pathological fnmatch
    patterns from reaching the dispatcher.
    """

    priority: list[Literal["critical", "high", "normal", "low"]] | None = None
    task_name: str | None = Field(default=None, max_length=500)
    task_name_pattern: str | None = Field(default=None, max_length=200)
    queue: str | None = Field(default=None, max_length=200)
    model_config = {"extra": "forbid"}


class DefaultSubscriptionCreate(BaseModel):
    trigger: str = Field(pattern=_TRIGGER_PATTERN)
    filters: SubscriptionFilters = Field(default_factory=SubscriptionFilters)
    in_app: bool = True
    project_channel_ids: list[uuid.UUID] = Field(default_factory=list)
    cooldown_seconds: int = Field(default=0, ge=0, le=86400)


class DefaultSubscriptionPublic(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    trigger: str
    filters: dict[str, Any]
    in_app: bool
    project_channel_ids: list[uuid.UUID]
    cooldown_seconds: int
    created_at: datetime


class DeliveryPublic(BaseModel):
    id: uuid.UUID
    subscription_id: uuid.UUID | None
    channel_id: uuid.UUID | None
    user_channel_id: uuid.UUID | None
    trigger: str
    task_id: str | None
    task_name: str | None
    status: str
    response_code: int | None
    error: str | None
    sent_at: datetime


class DeliveryListPublic(BaseModel):
    """Paged listing of delivery rows.

    Matches the envelope shape used by the other list endpoints
    (``RecentFailuresPublic``, etc.) - ``items`` + keyset
    ``next_cursor``. Cursor encoding mirrors ``home._encode_recent_failures_cursor``:
    ``"<iso8601>|<uuid_hex>"``.
    """

    items: list[DeliveryPublic]
    next_cursor: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SENSITIVE_CONFIG_KEYS = ("smtp_pass", "hmac_secret", "bot_token", "password")

# Telegram bot-token and chat-id regexes now live in the domain
# module (``z4j_brain.domain.notifications.channels``) so the
# project-channel and user-channel validators share one source of
# truth - see external-audit High #2.
_MASK = "••••••••"


def _mask_config(config: dict[str, Any]) -> dict[str, Any]:
    """Mask credential-like fields when returning channel configs."""
    safe = dict(config)
    for key in _SENSITIVE_CONFIG_KEYS:
        if key in safe and safe[key]:
            safe[key] = _MASK
    return safe


def _safe_merge_config(
    existing: dict[str, Any],
    incoming: dict[str, Any],
    *,
    mask: str,
) -> tuple[dict[str, Any], bool]:
    """Merge a PATCH config payload onto an existing channel config.

    HIGH-01 fix. Two related credential bugs are solved here:

    1. Clients that render a channel and POST it back unchanged send
       the MASK placeholder ("••••••••") in place of the real secret.
       Naive ``dict.update`` would overwrite the real password with
       bullets. We DROP any incoming value equal to the mask so the
       existing secret is preserved.

    2. If the caller changes the URL (e.g. from a legitimate webhook
       to ``http://attacker.com``) without also re-supplying the
       credentials, the attacker would inherit the signed payloads
       or SMTP password ("URL-pivot credential harvest"). When the
       url/webhook_url changes we forcibly clear every sensitive
       key from the merged config so the user must re-enter them.

    Returns ``(merged, url_changed)``.
    """
    merged = dict(existing or {})
    scrubbed: dict[str, Any] = {}
    for k, v in (incoming or {}).items():
        if k in _SENSITIVE_CONFIG_KEYS and v == mask:
            # Preserve existing secret - client just echoed the mask.
            continue
        scrubbed[k] = v

    # Detect URL change BEFORE applying the merge so we know whether
    # to wipe sensitive keys below.
    url_changed = False
    for url_key in ("url", "webhook_url"):
        if url_key in scrubbed and scrubbed[url_key] != merged.get(url_key):
            url_changed = True
            break

    merged.update(scrubbed)

    if url_changed:
        for sk in _SENSITIVE_CONFIG_KEYS:
            merged.pop(sk, None)

    return merged, url_changed


async def _validate_channel_config(
    channel_type: str,
    config: dict[str, Any] | None,
) -> None:
    """Validate the URL + headers in a (partial) channel config.

    Raises :class:`ConflictError` if anything is unsafe. Safe to call
    with a PATCH payload - only keys that are present are checked.
    """
    if not config:
        return
    if channel_type == "webhook":
        if "url" in config:
            err = await validate_webhook_url(config.get("url", ""))
            if err:
                raise ConflictError(f"unsafe URL: {err}")
        if "headers" in config:
            header_err, _ = validate_webhook_headers(config.get("headers"))
            if header_err:
                raise ConflictError(f"unsafe headers: {header_err}")
    elif channel_type == "slack":
        if "webhook_url" in config:
            err = await validate_webhook_url(config.get("webhook_url", ""))
            if err:
                raise ConflictError(f"unsafe URL: {err}")
    elif channel_type == "telegram":
        # Shared helper (domain/notifications/channels.py) so the
        # project-channel + user-channel validators stay in sync.
        # The earlier in-file regex has moved there - see R3 H7
        # + external-audit High #2 for rationale.
        from z4j_brain.domain.notifications.channels import (
            validate_telegram_config,
        )
        err = validate_telegram_config(config)
        if err:
            raise ConflictError(f"unsafe telegram config: {err}")
    elif channel_type == "email":
        # External-audit High #3 - block SMTP egress to private
        # IPs and non-allowlisted ports from both validators.
        from z4j_brain.domain.notifications.channels import (
            validate_smtp_config,
        )
        err = await validate_smtp_config(config)
        if err:
            raise ConflictError(f"unsafe email config: {err}")


async def _resolve_member_project(
    slug: str,
    user: "User",
    memberships: "MembershipRepository",
    projects: "ProjectRepository",
    *,
    min_role: ProjectRole = ProjectRole.VIEWER,
) -> uuid.UUID:
    """Verify membership and return the project id."""
    from z4j_brain.domain.policy_engine import PolicyEngine

    policy = PolicyEngine()
    project = await policy.get_project_or_404(projects, slug)
    await policy.require_member(
        memberships, user=user, project_id=project.id, min_role=min_role,
    )
    return project.id


def _channel_payload(c: Any) -> ChannelPublic:
    return ChannelPublic(
        id=c.id,
        project_id=c.project_id,
        name=c.name,
        type=c.type,
        config=_mask_config(c.config or {}),
        is_active=c.is_active,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _default_payload(d: Any) -> DefaultSubscriptionPublic:
    return DefaultSubscriptionPublic(
        id=d.id,
        project_id=d.project_id,
        trigger=d.trigger,
        filters=d.filters or {},
        in_app=d.in_app,
        project_channel_ids=list(d.project_channel_ids or []),
        cooldown_seconds=d.cooldown_seconds,
        created_at=d.created_at,
    )


def _delivery_payload(d: Any) -> DeliveryPublic:
    return DeliveryPublic(
        id=d.id,
        subscription_id=d.subscription_id,
        channel_id=d.channel_id,
        user_channel_id=d.user_channel_id,
        trigger=d.trigger,
        task_id=d.task_id,
        task_name=d.task_name,
        status=d.status,
        response_code=d.response_code,
        error=d.error,
        sent_at=d.sent_at,
    )


# ---------------------------------------------------------------------------
# Channels (project-scoped, shared)
# ---------------------------------------------------------------------------


@router.get("/channels", response_model=list[ChannelPublic])
async def list_channels(
    slug: str,
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
    db_session: "AsyncSession" = Depends(get_session),
) -> list[ChannelPublic]:
    """List the project's shared channels.

    All members can read - they may need to pick from this list
    when subscribing to events. Only admins can write.
    """
    from z4j_brain.persistence.repositories import (
        NotificationChannelRepository,
    )

    project_id = await _resolve_member_project(slug, user, memberships, projects)
    rows = await NotificationChannelRepository(db_session).list_for_project(project_id)
    return [_channel_payload(r) for r in rows]


@router.post(
    "/channels",
    response_model=ChannelPublic,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def create_channel(
    slug: str,
    body: ChannelCreate,
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
    db_session: "AsyncSession" = Depends(get_session),
) -> ChannelPublic:
    from z4j_brain.persistence.models.notification import NotificationChannel

    project_id = await _resolve_member_project(
        slug, user, memberships, projects, min_role=ProjectRole.ADMIN,
    )
    # SSRF / header validation BEFORE persisting. Blocks private-IP
    # webhooks and auth-header smuggling at the entry point.
    await _validate_channel_config(body.type, body.config)
    channel = NotificationChannel(
        project_id=project_id,
        name=body.name,
        type=body.type,
        config=body.config,
        is_active=body.is_active,
    )
    db_session.add(channel)
    await db_session.flush()
    try:
        await db_session.commit()
    except IntegrityError:
        await db_session.rollback()
        raise ConflictError("channel already exists") from None
    await db_session.refresh(channel)
    return _channel_payload(channel)


@router.patch(
    "/channels/{channel_id}",
    response_model=ChannelPublic,
    dependencies=[Depends(require_csrf)],
)
async def update_channel(
    slug: str,
    channel_id: uuid.UUID,
    body: ChannelUpdate,
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
    db_session: "AsyncSession" = Depends(get_session),
) -> ChannelPublic:
    from z4j_brain.errors import NotFoundError
    from z4j_brain.persistence.repositories import (
        NotificationChannelRepository,
    )

    project_id = await _resolve_member_project(
        slug, user, memberships, projects, min_role=ProjectRole.ADMIN,
    )
    channel = await NotificationChannelRepository(db_session).get_for_project(
        project_id, channel_id,
    )
    if channel is None:
        raise NotFoundError(
            "channel not found",
            details={"channel_id": str(channel_id)},
        )
    if body.name is not None:
        channel.name = body.name
    if body.config is not None:
        # Validate any incoming URL/headers BEFORE the merge so
        # unsafe patches never touch the DB.
        await _validate_channel_config(channel.type, body.config)
        # Merge instead of replace so admins don't have to re-enter
        # masked credentials. See _safe_merge_config for HIGH-01
        # details (mask-echo preservation + URL-pivot scrub).
        merged, _url_changed = _safe_merge_config(
            channel.config or {}, body.config, mask=_MASK,
        )
        channel.config = merged
    if body.is_active is not None:
        channel.is_active = body.is_active
    await db_session.flush()
    await db_session.commit()
    await db_session.refresh(channel)
    return _channel_payload(channel)


@router.delete(
    "/channels/{channel_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
async def delete_channel(
    slug: str,
    channel_id: uuid.UUID,
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
    db_session: "AsyncSession" = Depends(get_session),
) -> None:
    from sqlalchemy import delete

    from z4j_brain.persistence.models.notification import NotificationChannel

    project_id = await _resolve_member_project(
        slug, user, memberships, projects, min_role=ProjectRole.ADMIN,
    )

    # DATA-05: strip this channel id from every subscription that
    # referenced it BEFORE the channel row is gone. Otherwise the
    # dispatcher would see orphan UUIDs in user_subscriptions /
    # project_default_subscriptions and silently skip (or worse,
    # log scary "unknown channel" warnings per event).
    from z4j_brain.persistence.repositories import (
        ProjectDefaultSubscriptionRepository,
        UserSubscriptionRepository,
    )

    await UserSubscriptionRepository(db_session).strip_project_channel(
        project_id=project_id, channel_id=channel_id,
    )
    await ProjectDefaultSubscriptionRepository(db_session).strip_project_channel(
        project_id=project_id, channel_id=channel_id,
    )

    await db_session.execute(
        delete(NotificationChannel).where(
            NotificationChannel.id == channel_id,
            NotificationChannel.project_id == project_id,
        ),
    )
    await db_session.commit()


# ---------------------------------------------------------------------------
# Project default subscriptions (admin onboarding templates)
# ---------------------------------------------------------------------------


@router.get(
    "/defaults",
    response_model=list[DefaultSubscriptionPublic],
)
async def list_defaults(
    slug: str,
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
    db_session: "AsyncSession" = Depends(get_session),
) -> list[DefaultSubscriptionPublic]:
    from z4j_brain.persistence.repositories import (
        ProjectDefaultSubscriptionRepository,
    )

    project_id = await _resolve_member_project(
        slug, user, memberships, projects, min_role=ProjectRole.ADMIN,
    )
    rows = await ProjectDefaultSubscriptionRepository(
        db_session,
    ).list_for_project(project_id)
    return [_default_payload(r) for r in rows]


@router.post(
    "/defaults",
    response_model=DefaultSubscriptionPublic,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def create_default(
    slug: str,
    body: DefaultSubscriptionCreate,
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
    db_session: "AsyncSession" = Depends(get_session),
) -> DefaultSubscriptionPublic:
    from z4j_brain.persistence.models.notification import (
        ProjectDefaultSubscription,
    )
    from z4j_brain.persistence.repositories import (
        NotificationChannelRepository,
        ProjectDefaultSubscriptionRepository,
    )

    project_id = await _resolve_member_project(
        slug, user, memberships, projects, min_role=ProjectRole.ADMIN,
    )

    # Validate channel ids belong to this project (defaults can only
    # reference project channels, never user channels).
    if body.project_channel_ids:
        valid = await NotificationChannelRepository(
            db_session,
        ).get_many_for_project(project_id, body.project_channel_ids)
        if len(valid) != len(set(body.project_channel_ids)):
            raise ConflictError(
                "one or more channel_ids do not belong to this project",
            )

    # Enforce uniqueness on (project_id, trigger) at the API layer
    # so we can return a clean 409. Single-query LIMIT 1 check
    # against the backing unique index (POL-5).
    if await ProjectDefaultSubscriptionRepository(
        db_session,
    ).exists_for_project_trigger(project_id, body.trigger):
        raise ConflictError(
            "default subscription for this trigger already exists",
            details={"trigger": body.trigger},
        )

    default = ProjectDefaultSubscription(
        project_id=project_id,
        trigger=body.trigger,
        filters=body.filters.model_dump(exclude_none=True),
        in_app=body.in_app,
        project_channel_ids=body.project_channel_ids,
        cooldown_seconds=body.cooldown_seconds,
    )
    db_session.add(default)
    await db_session.flush()
    try:
        await db_session.commit()
    except IntegrityError:
        # HIGH-02: concurrent insert lost the check-then-insert race.
        await db_session.rollback()
        raise ConflictError(
            "default subscription for this trigger already exists",
            details={"trigger": body.trigger},
        ) from None
    await db_session.refresh(default)
    return _default_payload(default)


@router.delete(
    "/defaults/{default_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
async def delete_default(
    slug: str,
    default_id: uuid.UUID,
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
    db_session: "AsyncSession" = Depends(get_session),
) -> None:
    from sqlalchemy import delete

    from z4j_brain.persistence.models.notification import (
        ProjectDefaultSubscription,
    )

    project_id = await _resolve_member_project(
        slug, user, memberships, projects, min_role=ProjectRole.ADMIN,
    )
    await db_session.execute(
        delete(ProjectDefaultSubscription).where(
            ProjectDefaultSubscription.id == default_id,
            ProjectDefaultSubscription.project_id == project_id,
        ),
    )
    await db_session.commit()


# ---------------------------------------------------------------------------
# Delivery audit log (admin-only, read-only)
# ---------------------------------------------------------------------------


@router.get("/deliveries", response_model=DeliveryListPublic)
async def list_deliveries(
    slug: str,
    limit: int = Query(default=50, ge=1, le=500),
    cursor: str | None = Query(default=None),
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    projects: "ProjectRepository" = Depends(get_project_repo),
    db_session: "AsyncSession" = Depends(get_session),
) -> DeliveryListPublic:
    """Paged admin-visible delivery log with keyset pagination.

    Matches the shape of every other list endpoint - ``items`` +
    optional ``next_cursor``. The cursor encodes the ``(sent_at, id)``
    tuple to keep pages stable under concurrent insert / delete.
    See docs/PRODUCTION_READINESS_2026Q2.md POL-2.
    """
    from z4j_brain.api.home import (  # reuse the same encoder shape
        _decode_recent_failures_cursor as _decode_cursor,
        _encode_recent_failures_cursor as _encode_cursor,
    )
    from z4j_brain.persistence.repositories import (
        NotificationDeliveryRepository,
    )

    project_id = await _resolve_member_project(
        slug, user, memberships, projects, min_role=ProjectRole.ADMIN,
    )

    cursor_dt, cursor_id = _decode_cursor(cursor)
    # Fetch limit + 1 so we can detect whether there's a next page.
    rows = await NotificationDeliveryRepository(db_session).list_for_project(
        project_id,
        limit=limit + 1,
        cursor_sent_at=cursor_dt,
        cursor_id=cursor_id,
    )
    next_cursor: str | None = None
    if len(rows) > limit:
        overflow = rows[limit]
        next_cursor = _encode_cursor(overflow.sent_at, overflow.id)
        rows = rows[:limit]
    return DeliveryListPublic(
        items=[_delivery_payload(r) for r in rows],
        next_cursor=next_cursor,
    )


__all__ = ["router"]
