"""``projects`` table - logical grouping of agents and events.

A project corresponds roughly to "one app deployment" - a Django
project, a Flask service, etc. Tokens are project-scoped, retention
is per-project, and dashboard navigation is project-scoped.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from z4j_brain.persistence.base import Base
from z4j_brain.persistence.models._mixins import PKMixin, TimestampsMixin
from z4j_brain.persistence.types import jsonb


class Project(PKMixin, TimestampsMixin, Base):
    """A z4j project.

    Attributes:
        slug: URL-safe identifier, also surfaced in agent token
            prefixes. Format enforced as a CHECK constraint at the
            database level - defence in depth against SQL-injection
            attempts via slug routing.
        name: Human-readable display name.
        description: Optional free-form description.
        environment: Free-form label like ``production`` / ``staging``.
        timezone: Default timezone for dashboard rendering.
        retention_days: Per-project event retention. The
            RetentionWorker drops event partitions older than this.
        is_active: Soft-disable flag.
    """

    __tablename__ = "projects"

    slug: Mapped[str] = mapped_column(
        String(63), nullable=False, unique=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    environment: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="production",
        server_default="production",
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC",
    )
    retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
    )

    # ------------------------------------------------------------------
    # Future columns (reserved, unused until Phase 2/3)
    # ------------------------------------------------------------------
    settings: Mapped[dict[str, Any] | None] = mapped_column(
        jsonb(), nullable=True,
    )  # Per-project config overrides (Phase 2)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True,
    )  # Org hierarchy (Phase 3)

    __table_args__ = (
        # The CHECK constraint that enforces the slug regex is added
        # in the alembic migration as raw SQL - Postgres uses ``~``
        # which SQLite cannot parse, so it has no place in the
        # cross-dialect model declaration.
        Index("ix_projects_active", "is_active"),
    )


__all__ = ["Project"]
