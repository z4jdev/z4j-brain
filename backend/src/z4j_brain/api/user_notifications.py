"""User-scoped notification endpoints.

All routes are under ``/api/v1/user/`` and operate on the
authenticated user's own data only.

Three resources:

- ``/user/channels``       - personal delivery destinations
- ``/user/subscriptions``  - per-(project, trigger) delivery rules
- ``/user/notifications``  - in-app inbox (the bell)

The user can ONLY ever see / write their own resources. Project
admin permissions don't grant access to other users' personal
data even within projects they administer.
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
    get_session,
    require_csrf,
)
from z4j_brain.domain.notifications.channels import (
    validate_smtp_config,
    validate_telegram_config,
    validate_webhook_headers,
    validate_webhook_url,
)
from z4j_brain.errors import ConflictError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from z4j_brain.persistence.models import User
    from z4j_brain.persistence.repositories import MembershipRepository


router = APIRouter(prefix="/user", tags=["user-notifications"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


_TRIGGER_PATTERN = (
    r"^(task\.failed|task\.succeeded|task\.retried|"
    r"task\.slow|agent\.offline|agent\.online)$"
)
_CHANNEL_TYPE_PATTERN = r"^(webhook|email|slack|telegram)$"

_SENSITIVE_KEYS = ("smtp_pass", "hmac_secret", "bot_token", "password")
_MASK = "••••••••"


def _mask(config: dict[str, Any]) -> dict[str, Any]:
    safe = dict(config)
    for k in _SENSITIVE_KEYS:
        if k in safe and safe[k]:
            safe[k] = _MASK
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
        if k in _SENSITIVE_KEYS and v == mask:
            # Preserve existing secret - client just echoed the mask.
            continue
        scrubbed[k] = v

    url_changed = False
    for url_key in ("url", "webhook_url"):
        if url_key in scrubbed and scrubbed[url_key] != merged.get(url_key):
            url_changed = True
            break

    merged.update(scrubbed)

    if url_changed:
        for sk in _SENSITIVE_KEYS:
            merged.pop(sk, None)

    return merged, url_changed


async def _validate_channel_config(
    channel_type: str,
    config: dict[str, Any] | None,
) -> None:
    """Validate a user channel config. Raises ``ConflictError`` on
    unsafe input. Only keys present in ``config`` are checked, so
    PATCH payloads work too.

    Previously this validator only covered webhook + slack - the
    telegram + email paths slipped through unchecked, giving any
    authenticated user an SSRF and a blind SMTP egress primitive
    on their own channel (external audit High #2 + #3). Every
    channel type is now validated via the shared domain-level
    helpers so the project-admin and user paths stay in sync.
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
        err = validate_telegram_config(config)
        if err:
            raise ConflictError(f"unsafe telegram config: {err}")
    elif channel_type == "email":
        err = await validate_smtp_config(config)
        if err:
            raise ConflictError(f"unsafe email config: {err}")


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


# -- User channels ----------------------------------------------------------


class UserChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(pattern=_CHANNEL_TYPE_PATTERN)
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class UserChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, Any] | None = None
    is_active: bool | None = None


class UserChannelPublic(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    config: dict[str, Any]
    is_verified: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


# -- User subscriptions -----------------------------------------------------


class UserSubscriptionCreate(BaseModel):
    # ``extra=forbid`` rejects unknown body keys so a future
    # refactor that accidentally re-adds a ``user_id`` /
    # privilege-controlling field can't silently bypass the
    # membership check (R3 M11 defence in depth - the caller's
    # identity is already established from the session cookie).
    model_config = {"extra": "forbid"}

    project_id: uuid.UUID
    trigger: str = Field(pattern=_TRIGGER_PATTERN)
    filters: SubscriptionFilters = Field(default_factory=SubscriptionFilters)
    in_app: bool = True
    project_channel_ids: list[uuid.UUID] = Field(default_factory=list)
    user_channel_ids: list[uuid.UUID] = Field(default_factory=list)
    cooldown_seconds: int = Field(default=0, ge=0, le=86400)


class UserSubscriptionUpdate(BaseModel):
    filters: SubscriptionFilters | None = None
    in_app: bool | None = None
    project_channel_ids: list[uuid.UUID] | None = None
    user_channel_ids: list[uuid.UUID] | None = None
    cooldown_seconds: int | None = Field(default=None, ge=0, le=86400)
    muted_until: datetime | None = None
    is_active: bool | None = None


class UserSubscriptionPublic(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID
    trigger: str
    filters: dict[str, Any]
    in_app: bool
    project_channel_ids: list[uuid.UUID]
    user_channel_ids: list[uuid.UUID]
    muted_until: datetime | None
    cooldown_seconds: int
    last_fired_at: datetime | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# -- In-app notifications ---------------------------------------------------


class UserNotificationPublic(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    subscription_id: uuid.UUID | None
    trigger: str
    reason: str
    title: str
    body: str | None
    data: dict[str, Any]
    read_at: datetime | None
    created_at: datetime


class UnreadCountPublic(BaseModel):
    unread: int


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _channel_payload(c: Any) -> UserChannelPublic:
    return UserChannelPublic(
        id=c.id,
        name=c.name,
        type=c.type,
        config=_mask(c.config or {}),
        is_verified=c.is_verified,
        is_active=c.is_active,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _subscription_payload(s: Any) -> UserSubscriptionPublic:
    return UserSubscriptionPublic(
        id=s.id,
        user_id=s.user_id,
        project_id=s.project_id,
        trigger=s.trigger,
        filters=s.filters or {},
        in_app=s.in_app,
        project_channel_ids=list(s.project_channel_ids or []),
        user_channel_ids=list(s.user_channel_ids or []),
        muted_until=s.muted_until,
        cooldown_seconds=s.cooldown_seconds,
        last_fired_at=s.last_fired_at,
        is_active=s.is_active,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def _notification_payload(n: Any) -> UserNotificationPublic:
    return UserNotificationPublic(
        id=n.id,
        project_id=n.project_id,
        subscription_id=n.subscription_id,
        trigger=n.trigger,
        reason=n.reason,
        title=n.title,
        body=n.body,
        data=n.data or {},
        read_at=n.read_at,
        created_at=n.created_at,
    )


# ---------------------------------------------------------------------------
# /user/channels
# ---------------------------------------------------------------------------


@router.get("/channels", response_model=list[UserChannelPublic])
async def list_user_channels(
    user: "User" = Depends(get_current_user),
    db_session: "AsyncSession" = Depends(get_session),
) -> list[UserChannelPublic]:
    from z4j_brain.persistence.repositories import UserChannelRepository

    rows = await UserChannelRepository(db_session).list_for_user(user.id)
    return [_channel_payload(r) for r in rows]


@router.post(
    "/channels",
    response_model=UserChannelPublic,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def create_user_channel(
    body: UserChannelCreate,
    user: "User" = Depends(get_current_user),
    db_session: "AsyncSession" = Depends(get_session),
) -> UserChannelPublic:
    from z4j_brain.persistence.models.notification import UserChannel

    # SSRF / header validation BEFORE persisting. Blocks private-IP
    # webhooks and auth-header smuggling at the entry point.
    await _validate_channel_config(body.type, body.config)
    channel = UserChannel(
        user_id=user.id,
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
    response_model=UserChannelPublic,
    dependencies=[Depends(require_csrf)],
)
async def update_user_channel(
    channel_id: uuid.UUID,
    body: UserChannelUpdate,
    user: "User" = Depends(get_current_user),
    db_session: "AsyncSession" = Depends(get_session),
) -> UserChannelPublic:
    from z4j_brain.errors import NotFoundError
    from z4j_brain.persistence.repositories import UserChannelRepository

    channel = await UserChannelRepository(db_session).get_for_user(
        user.id, channel_id,
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
        # See _safe_merge_config for HIGH-01 details (mask-echo
        # preservation + URL-pivot scrub).
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
async def delete_user_channel(
    channel_id: uuid.UUID,
    user: "User" = Depends(get_current_user),
    db_session: "AsyncSession" = Depends(get_session),
) -> None:
    from sqlalchemy import delete

    from z4j_brain.persistence.models.notification import UserChannel
    from z4j_brain.persistence.repositories import UserSubscriptionRepository

    # DATA-05: scrub this channel id from the user's own
    # subscriptions before the channel row is gone, so no
    # subscription holds an orphan UUID in user_channel_ids. User
    # channels are owned by a single user, so the cleanup is scoped
    # to that user only.
    await UserSubscriptionRepository(db_session).strip_user_channel(
        user_id=user.id, channel_id=channel_id,
    )

    await db_session.execute(
        delete(UserChannel).where(
            UserChannel.id == channel_id,
            UserChannel.user_id == user.id,
        ),
    )
    await db_session.commit()


# ---------------------------------------------------------------------------
# /user/subscriptions
# ---------------------------------------------------------------------------


@router.get(
    "/subscriptions",
    response_model=list[UserSubscriptionPublic],
)
async def list_user_subscriptions(
    project_id: uuid.UUID | None = Query(default=None),
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    db_session: "AsyncSession" = Depends(get_session),
) -> list[UserSubscriptionPublic]:
    """List the caller's subscriptions, optionally filtered to one project.

    When ``project_id`` is supplied the caller must currently be a
    member of that project (R3 finding M10). Today the underlying
    query already scopes by ``user.id`` so a non-member sees only an
    empty list, but the explicit check documents intent and prevents
    a regression if ``list_for_user`` is ever widened to span users.
    """
    from z4j_brain.persistence.repositories import UserSubscriptionRepository

    if project_id is not None and not user.is_admin:
        membership = await memberships.get_for_user_project(
            user_id=user.id, project_id=project_id,
        )
        if membership is None:
            from z4j_brain.errors import AuthorizationError

            raise AuthorizationError(
                "no membership on this project",
                details={"project_id": str(project_id)},
            )
    rows = await UserSubscriptionRepository(db_session).list_for_user(
        user.id, project_id=project_id,
    )
    return [_subscription_payload(r) for r in rows]


@router.post(
    "/subscriptions",
    response_model=UserSubscriptionPublic,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def create_user_subscription(
    body: UserSubscriptionCreate,
    user: "User" = Depends(get_current_user),
    memberships: "MembershipRepository" = Depends(get_membership_repo),
    db_session: "AsyncSession" = Depends(get_session),
) -> UserSubscriptionPublic:
    from z4j_brain.errors import ForbiddenError
    from z4j_brain.persistence.models.notification import UserSubscription
    from z4j_brain.persistence.repositories import (
        NotificationChannelRepository,
        UserChannelRepository,
        UserSubscriptionRepository,
    )

    # User must be a member of the project they're subscribing to.
    membership = await memberships.get_for_user_project(
        user_id=user.id, project_id=body.project_id,
    )
    if membership is None:
        raise ForbiddenError(
            "you are not a member of this project",
            details={"project_id": str(body.project_id)},
        )

    # Validate channel ids: project channels must belong to the
    # project, user channels must be owned by this user.
    if body.project_channel_ids:
        valid = await NotificationChannelRepository(
            db_session,
        ).get_many_for_project(body.project_id, body.project_channel_ids)
        if len(valid) != len(set(body.project_channel_ids)):
            raise ConflictError(
                "one or more project_channel_ids do not belong to this project",
            )
    if body.user_channel_ids:
        valid = await UserChannelRepository(db_session).get_many_for_user(
            user.id, body.user_channel_ids,
        )
        if len(valid) != len(set(body.user_channel_ids)):
            raise ConflictError(
                "one or more user_channel_ids do not belong to you",
            )

    sub_repo = UserSubscriptionRepository(db_session)
    existing = await sub_repo.get_by_unique(
        user_id=user.id, project_id=body.project_id, trigger=body.trigger,
    )
    if existing is not None:
        raise ConflictError(
            "you already have a subscription for this trigger on this project",
            details={"trigger": body.trigger},
        )

    sub = UserSubscription(
        user_id=user.id,
        project_id=body.project_id,
        trigger=body.trigger,
        filters=body.filters.model_dump(exclude_none=True),
        in_app=body.in_app,
        project_channel_ids=body.project_channel_ids,
        user_channel_ids=body.user_channel_ids,
        cooldown_seconds=body.cooldown_seconds,
    )
    db_session.add(sub)
    await db_session.flush()
    try:
        await db_session.commit()
    except IntegrityError:
        # HIGH-02: concurrent insert lost the check-then-insert race.
        await db_session.rollback()
        raise ConflictError(
            "subscription for this trigger already exists",
            details={"trigger": body.trigger},
        ) from None
    await db_session.refresh(sub)
    return _subscription_payload(sub)


@router.patch(
    "/subscriptions/{sub_id}",
    response_model=UserSubscriptionPublic,
    dependencies=[Depends(require_csrf)],
)
async def update_user_subscription(
    sub_id: uuid.UUID,
    body: UserSubscriptionUpdate,
    user: "User" = Depends(get_current_user),
    db_session: "AsyncSession" = Depends(get_session),
) -> UserSubscriptionPublic:
    from z4j_brain.errors import NotFoundError
    from z4j_brain.persistence.repositories import (
        NotificationChannelRepository,
        UserChannelRepository,
        UserSubscriptionRepository,
    )

    sub = await UserSubscriptionRepository(db_session).get_for_user(
        user.id, sub_id,
    )
    if sub is None:
        raise NotFoundError(
            "subscription not found",
            details={"subscription_id": str(sub_id)},
        )

    if body.filters is not None:
        sub.filters = body.filters.model_dump(exclude_none=True)
    if body.in_app is not None:
        sub.in_app = body.in_app
    if body.project_channel_ids is not None:
        if body.project_channel_ids:
            valid = await NotificationChannelRepository(
                db_session,
            ).get_many_for_project(sub.project_id, body.project_channel_ids)
            if len(valid) != len(set(body.project_channel_ids)):
                raise ConflictError(
                    "one or more project_channel_ids do not belong to this project",
                )
        sub.project_channel_ids = body.project_channel_ids
    if body.user_channel_ids is not None:
        if body.user_channel_ids:
            valid = await UserChannelRepository(db_session).get_many_for_user(
                user.id, body.user_channel_ids,
            )
            if len(valid) != len(set(body.user_channel_ids)):
                raise ConflictError(
                    "one or more user_channel_ids do not belong to you",
                )
        sub.user_channel_ids = body.user_channel_ids
    if body.cooldown_seconds is not None:
        sub.cooldown_seconds = body.cooldown_seconds
    if body.muted_until is not None:
        sub.muted_until = body.muted_until
    if body.is_active is not None:
        sub.is_active = body.is_active
    await db_session.flush()
    await db_session.commit()
    await db_session.refresh(sub)
    return _subscription_payload(sub)


@router.delete(
    "/subscriptions/{sub_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
async def delete_user_subscription(
    sub_id: uuid.UUID,
    user: "User" = Depends(get_current_user),
    db_session: "AsyncSession" = Depends(get_session),
) -> None:
    from sqlalchemy import delete

    from z4j_brain.persistence.models.notification import UserSubscription

    await db_session.execute(
        delete(UserSubscription).where(
            UserSubscription.id == sub_id,
            UserSubscription.user_id == user.id,
        ),
    )
    await db_session.commit()


# ---------------------------------------------------------------------------
# /user/notifications  (the in-app inbox)
# ---------------------------------------------------------------------------


@router.get(
    "/notifications",
    response_model=list[UserNotificationPublic],
)
async def list_user_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    user: "User" = Depends(get_current_user),
    db_session: "AsyncSession" = Depends(get_session),
) -> list[UserNotificationPublic]:
    from z4j_brain.persistence.repositories import UserNotificationRepository

    rows = await UserNotificationRepository(db_session).list_for_user(
        user.id, unread_only=unread_only, limit=limit,
    )
    return [_notification_payload(r) for r in rows]


@router.get(
    "/notifications/unread-count",
    response_model=UnreadCountPublic,
)
async def user_unread_count(
    user: "User" = Depends(get_current_user),
    db_session: "AsyncSession" = Depends(get_session),
) -> UnreadCountPublic:
    from z4j_brain.persistence.repositories import UserNotificationRepository

    count = await UserNotificationRepository(db_session).unread_count(user.id)
    return UnreadCountPublic(unread=count)


@router.post(
    "/notifications/{notification_id}/read",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
async def mark_user_notification_read(
    notification_id: uuid.UUID,
    user: "User" = Depends(get_current_user),
    db_session: "AsyncSession" = Depends(get_session),
) -> None:
    from z4j_brain.persistence.repositories import UserNotificationRepository

    await UserNotificationRepository(db_session).mark_read(
        user_id=user.id, notification_id=notification_id,
    )
    await db_session.commit()


@router.post(
    "/notifications/read-all",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
async def mark_all_user_notifications_read(
    user: "User" = Depends(get_current_user),
    db_session: "AsyncSession" = Depends(get_session),
) -> None:
    from z4j_brain.persistence.repositories import UserNotificationRepository

    await UserNotificationRepository(db_session).mark_all_read(user.id)
    await db_session.commit()


__all__ = ["router"]
