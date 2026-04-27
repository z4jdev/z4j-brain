"""gRPC server lifecycle for the SchedulerService.

Started by the brain's main lifespan when
``Z4J_SCHEDULER_GRPC_ENABLED`` is set. Bound to
``Z4J_SCHEDULER_GRPC_BIND_HOST`` :
``Z4J_SCHEDULER_GRPC_BIND_PORT`` (default ``0.0.0.0:7701``).

Cleanly stops on brain shutdown - drains in-flight RPCs (with
deadline), closes WatchSchedules streams, releases the gRPC
runtime.

Phase 1 implementation lands here.
"""

from __future__ import annotations

# Phase 1 implementation: grpc.aio server boot, handler
# registration, lifespan integration with brain's main.py.
