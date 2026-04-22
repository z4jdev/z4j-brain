"""Schema version tracking and startup compatibility check.

After Alembic migrations run and before the app starts serving,
this module:

1. Reads the ``schema_version`` from the ``z4j_meta`` table
2. Compares it to the current code version
3. If the DB schema is NEWER than the code -> refuse to start
   (prevents old code from silently corrupting a forward-migrated DB)
4. If the DB schema is OLDER or equal -> update the record and proceed

This is the Grafana/Sentry approach: forward-only migrations,
restore-from-backup for rollback. No downgrade migrations in
production.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from z4j_brain import __version__
from z4j_brain.persistence.models.meta import Z4JMeta

logger = logging.getLogger("z4j.brain.startup_version")


class SchemaVersionError(RuntimeError):
    """Raised when the database schema is newer than the running code.

    This means someone downgraded the z4j-brain package without
    restoring the database to a compatible state. The only safe
    fix is to either upgrade the code or restore a database backup.
    """


def _parse_calver(version: str) -> tuple[int, int, int]:
    """Parse CalVer 'YYYY.M.PATCH' or 'YYYY.M.PATCHaN' into a comparable tuple."""
    # Strip pre-release suffix (a1, b1, rc1)
    clean = version.split("a")[0].split("b")[0].split("rc")[0]
    parts = clean.split(".")
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
    except (ValueError, IndexError):
        return (0, 0, 0)


async def check_and_update_schema_version(session: AsyncSession) -> None:
    """Check schema compatibility and update the version record.

    Called during app startup, after migrations have been applied.

    Raises:
        SchemaVersionError: if the DB schema is newer than the code.
    """
    code_version = __version__
    code_tuple = _parse_calver(code_version)

    # Read the stored schema version.
    result = await session.execute(
        select(Z4JMeta).where(Z4JMeta.key == "schema_version"),
    )
    meta = result.scalar_one_or_none()

    if meta is None:
        # First run - no schema version stored yet. Create it.
        session.add(Z4JMeta(key="schema_version", value=code_version))
        session.add(Z4JMeta(key="installed_at", value=datetime.now(UTC).isoformat()))
        session.add(Z4JMeta(key="last_upgraded_at", value=datetime.now(UTC).isoformat()))
        await session.commit()
        logger.info(
            "z4j schema version initialized (version=%s)",
            code_version,
        )
        return

    db_version = meta.value
    db_tuple = _parse_calver(db_version)

    if db_tuple > code_tuple:
        # Database was migrated by a NEWER version of z4j-brain.
        # Running old code against a forward-migrated schema risks
        # data corruption. Refuse to start.
        raise SchemaVersionError(
            f"Database schema version ({db_version}) is newer than "
            f"the running code ({code_version}). This means the "
            f"brain was previously running a newer version. "
            f"Either upgrade z4j-brain to >= {db_version} or "
            f"restore the database from a backup taken before the "
            f"upgrade. Do NOT run old code against a forward-migrated "
            f"database - it may corrupt your data."
        )

    if db_tuple < code_tuple:
        # Upgrading - update the stored version.
        meta.value = code_version
        # Update last_upgraded_at.
        result = await session.execute(
            select(Z4JMeta).where(Z4JMeta.key == "last_upgraded_at"),
        )
        upgraded_meta = result.scalar_one_or_none()
        if upgraded_meta:
            upgraded_meta.value = datetime.now(UTC).isoformat()
        else:
            session.add(Z4JMeta(key="last_upgraded_at", value=datetime.now(UTC).isoformat()))
        await session.commit()
        logger.info(
            "z4j schema version upgraded (from=%s to=%s)",
            db_version,
            code_version,
        )
    else:
        # Same version - nothing to do.
        logger.debug(
            "z4j schema version matches code (version=%s)",
            db_version,
        )


__all__ = ["SchemaVersionError", "check_and_update_schema_version"]
