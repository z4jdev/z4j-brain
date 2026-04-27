"""Per-RPC handler implementations.

Each handler is a thin translator:

1. Convert protobuf request -> Python kwargs
2. Call the appropriate domain service (CommandDispatcher,
   SchedulesRepository, AuditService) - reusing existing brain code
3. Convert Python result -> protobuf response

No business logic lives here - that stays in
``z4j_brain.domain.*``. The handlers are pure marshalling.

Per ``docs/SCHEDULER.md §13.2``, each handler also writes the
appropriate audit row using the existing HMAC-chained ``audit_log``.

Phase 1 implementation:

- ``ListSchedules`` -> ``SchedulesRepository.list_for_scheduler``
- ``WatchSchedules`` -> Postgres LISTEN on ``schedules_changed``
- ``FireSchedule`` -> ``CommandDispatcher.issue(action='submit_task', ...)``
- ``TriggerSchedule`` -> same with audit_action='schedule.trigger_now'
- ``AcknowledgeFireResult`` -> ``SchedulesRepository.record_fire_result``
- ``Ping`` -> trivially returns version + current timestamp
"""

from __future__ import annotations

# Phase 1 implementation per the per-handler design above.
