"""``PartitionCreatorWorker`` - pre-creates daily events partitions.

The ``events`` table is PARTITION BY RANGE (occurred_at) with one
child table per day (e.g. ``events_2026_04_12``). The initial
migration pre-creates 7 days of partitions. After that this worker
takes over: once per hour it ensures partitions exist for today
and the next ``_LOOKAHEAD_DAYS`` days. If a partition already
exists the CREATE is a no-op (guarded by IF NOT EXISTS).

Without this worker, every INSERT into ``events`` after the
pre-created range expires fails with "no partition of relation
events found for row" - a production-fatal condition that was
flagged by the B7 audit.

The worker also handles retention: partitions older than
``settings.event_retention_days`` are dropped. This is the only
code path that deletes event data.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text

if TYPE_CHECKING:
    from z4j_brain.persistence.database import DatabaseManager
    from z4j_brain.settings import Settings


logger = structlog.get_logger("z4j.brain.workers.partition_creator")

_LOOKAHEAD_DAYS: int = 7


class PartitionCreatorWorker:
    """Periodic events-partition manager.

    Creates future partitions and drops expired ones. Designed to
    run hourly - fast enough that a missed tick doesn't leave a gap,
    infrequent enough that the DDL overhead is negligible.
    """

    def __init__(
        self,
        *,
        db: "DatabaseManager",
        settings: "Settings",
    ) -> None:
        self._db = db
        self._retention_days = settings.event_retention_days

    async def tick(self) -> None:
        """One sweep: create future partitions + drop expired ones."""
        today = date.today()

        async with self._db.session() as session:
            bind = await session.connection()
            dialect = bind.dialect.name

            if dialect != "postgresql":
                return

            # Create partitions for today + lookahead.
            created = 0
            for offset in range(_LOOKAHEAD_DAYS + 1):
                day = today + timedelta(days=offset)
                partition_name = f"events_{day.strftime('%Y_%m_%d')}"
                range_start = day.isoformat()
                range_end = (day + timedelta(days=1)).isoformat()

                await session.execute(
                    text(
                        f"CREATE TABLE IF NOT EXISTS {partition_name} "
                        f"PARTITION OF events "
                        f"FOR VALUES FROM ('{range_start}') "
                        f"TO ('{range_end}')"
                    ),
                )
                created += 1

            # Drop expired partitions.
            dropped = 0
            if self._retention_days > 0:
                cutoff = today - timedelta(days=self._retention_days)
                # Find all partition children of `events` that are
                # older than the cutoff. We query pg_inherits to get
                # the list, then drop each one.
                result = await session.execute(
                    text(
                        "SELECT c.relname FROM pg_inherits i "
                        "JOIN pg_class c ON i.inhrelid = c.oid "
                        "JOIN pg_class p ON i.inhparent = p.oid "
                        "WHERE p.relname = 'events' "
                        "AND c.relname LIKE 'events_20%' "
                        "ORDER BY c.relname"
                    ),
                )
                for (partition_name,) in result.all():
                    # Parse date from partition name: events_YYYY_MM_DD
                    try:
                        parts = partition_name.replace("events_", "").split("_")
                        partition_date = date(
                            int(parts[0]), int(parts[1]), int(parts[2]),
                        )
                    except (ValueError, IndexError):
                        continue
                    if partition_date < cutoff:
                        await session.execute(
                            text(f"DROP TABLE IF EXISTS {partition_name}"),
                        )
                        dropped += 1

            await session.commit()

        if created or dropped:
            logger.info(
                "z4j partition creator tick",
                partitions_ensured=created,
                partitions_dropped=dropped,
                retention_days=self._retention_days,
            )


__all__ = ["PartitionCreatorWorker"]
