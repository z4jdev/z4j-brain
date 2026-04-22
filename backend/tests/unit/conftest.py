"""Shared pytest fixtures for the brain backend.

Tests build the brain on top of an in-memory ``sqlite+aiosqlite://``
engine - no Postgres required for unit tests. Integration tests in
B7 will use a real Postgres 18 container.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from z4j_brain.main import create_app
from z4j_brain.settings import Settings


@pytest.fixture
def brain_settings() -> Settings:
    """A valid Settings instance backed by in-memory SQLite."""
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        secret=secrets.token_urlsafe(48),  # type: ignore[arg-type]
        session_secret=secrets.token_urlsafe(48),  # type: ignore[arg-type]
        log_json=False,
        environment="dev",
    )


@pytest.fixture
async def brain_app(brain_settings: Settings):
    """Yield a configured FastAPI app on an in-memory SQLite engine."""
    engine = create_async_engine(
        brain_settings.database_url,
        future=True,
    )
    app = create_app(brain_settings, engine=engine)
    yield app
    await engine.dispose()


@pytest.fixture
async def client(brain_app) -> AsyncIterator[AsyncClient]:
    """Async HTTPX client wired to the brain app via ASGITransport."""
    transport = ASGITransport(app=brain_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as ac:
        yield ac
