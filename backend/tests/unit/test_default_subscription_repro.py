"""Reproducer for the production 500 on Add Default Subscription.

Screenshot from operator: clicking "Create Default" in the
dashboard's Project Settings → Defaults page produced three
"Failed: the brain encountered an unexpected error" toasts. Form
shape:

- Trigger: "Task failed"  → ``task.failed``
- In-app: True
- Project channels: telegram + slack + email (3 ids)
- Cooldown: 300

These tests poke the same code path with the same shape to find
out which branch is throwing.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from z4j_brain.auth.passwords import PasswordHasher
from z4j_brain.auth.sessions import SessionCookieCodec, cookie_name
from z4j_brain.main import create_app
from z4j_brain.persistence.base import Base
from z4j_brain.persistence import models  # noqa: F401
from z4j_brain.persistence.models import (
    Membership,
    NotificationChannel,
    Project,
    Session,
    User,
)
from z4j_brain.persistence.enums import ProjectRole
from z4j_brain.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        secret=secrets.token_urlsafe(48),  # type: ignore[arg-type]
        session_secret=secrets.token_urlsafe(48),  # type: ignore[arg-type]
        environment="dev",
        log_json=False,
        argon2_time_cost=1,
        argon2_memory_cost=8192,
        login_min_duration_ms=10,
        registry_backend="local",
        metrics_public=True,
        disable_spa_fallback=True,
    )


@pytest.fixture
async def brain_app(settings: Settings):
    engine = create_async_engine(
        settings.database_url,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = create_app(settings, engine=engine)
    yield app
    await engine.dispose()


async def _seed(brain_app, settings: Settings):
    """Admin user + project + three project channels matching the screenshot."""
    db = brain_app.state.db
    hasher = PasswordHasher(settings)
    project_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    csrf = secrets.token_urlsafe(32)
    channel_ids = {
        "telegram": uuid.uuid4(),
        "slack": uuid.uuid4(),
        "email": uuid.uuid4(),
    }

    async with db.session() as s:
        s.add_all(
            [
                Project(id=project_id, slug="fragai", name="FragAI"),
                User(
                    id=user_id,
                    email=f"u-{uuid.uuid4().hex[:8]}@example.com",
                    password_hash=hasher.hash("correct horse battery staple 9"),
                    is_admin=True,
                    is_active=True,
                ),
                Session(
                    id=session_id,
                    user_id=user_id,
                    csrf_token=csrf,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                    ip_at_issue="127.0.0.1",
                    user_agent_at_issue="test",
                ),
                NotificationChannel(
                    id=channel_ids["telegram"],
                    project_id=project_id,
                    name="Frag.ai Fail Task TG",
                    type="telegram",
                    config={"bot_token": "x", "chat_id": "y"},
                    enabled=True,
                ),
                NotificationChannel(
                    id=channel_ids["slack"],
                    project_id=project_id,
                    name="Fragi.ai Failed Task",
                    type="slack",
                    config={"webhook_url": "https://hooks.slack.example.com/x"},
                    enabled=True,
                ),
                NotificationChannel(
                    id=channel_ids["email"],
                    project_id=project_id,
                    name="Frag.ai Fail Task Gmail",
                    type="email",
                    config={"to": "ops@example.com"},
                    enabled=True,
                ),
            ],
        )
        await s.commit()

    return {
        "project_id": project_id,
        "user_id": user_id,
        "session_id": session_id,
        "csrf": csrf,
        "channel_ids": channel_ids,
    }


def _client(brain_app, settings: Settings, seed: dict):
    from httpx import ASGITransport, AsyncClient

    from z4j_brain.auth.csrf import csrf_cookie_name

    transport = ASGITransport(app=brain_app)
    ac = AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"X-CSRF-Token": seed["csrf"]},
    )
    codec = SessionCookieCodec(settings)
    ac.cookies.set(
        cookie_name(environment=settings.environment),
        codec.encode(seed["session_id"]),
    )
    ac.cookies.set(
        csrf_cookie_name(environment=settings.environment),
        seed["csrf"],
    )
    return ac


class TestProductionRepro:
    @pytest.mark.asyncio
    async def test_create_default_with_three_channels(
        self, settings: Settings, brain_app,
    ) -> None:
        # Exact shape from the screenshot.
        seed = await _seed(brain_app, settings)
        async with _client(brain_app, settings, seed) as client:
            r = await client.post(
                "/api/v1/projects/fragai/notifications/defaults",
                json={
                    "trigger": "task.failed",
                    "in_app": True,
                    "project_channel_ids": [
                        str(seed["channel_ids"]["telegram"]),
                        str(seed["channel_ids"]["slack"]),
                        str(seed["channel_ids"]["email"]),
                    ],
                    "cooldown_seconds": 300,
                },
            )
        # Print the body so the failure surfaces the actual error.
        if r.status_code != 201:
            print(f"\n!!! status={r.status_code}\nbody={r.text}")
        assert r.status_code == 201, r.text
