"""Host header validation middleware.

Rejects requests whose ``Host`` header is not in
``settings.allowed_hosts``. Defends against:

- **Cache poisoning** - an attacker who can hit the brain with a
  spoofed ``Host: evil.example.com`` could otherwise cause the
  brain to bake links pointing at ``evil.example.com`` into
  responses (the dashboard reads ``settings.public_url``, but
  password-reset emails or webhooks built from request URL would
  be vulnerable).
- **Routing leakage** - same threat for any future feature that
  uses ``request.url`` to build absolute URLs.

In ``environment="dev"`` we add ``localhost`` and ``127.0.0.1``
automatically so contributors do not have to set the env var to
run the test suite.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

if TYPE_CHECKING:
    from z4j_brain.settings import Settings

logger = logging.getLogger("z4j.brain.host_validation")

#: Always-allowed hosts in dev mode (in addition to the configured
#: list). Tests do not have to set ``allowed_hosts``.
_DEV_DEFAULTS: frozenset[str] = frozenset(
    {"localhost", "127.0.0.1", "[::1]", "testserver"},
)


class HostValidationMiddleware(BaseHTTPMiddleware):
    """Reject requests with an unrecognised Host header.

    Strips the optional port suffix before comparing - operators
    configure ``allowed_hosts=["z4j.example.com"]``, NOT
    ``["z4j.example.com:7700"]``.
    """

    def __init__(self, app, *, settings: Settings) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        is_dev = settings.environment == "dev"
        configured = {h.lower() for h in settings.allowed_hosts}
        if is_dev:
            configured |= _DEV_DEFAULTS
        self._allowed: frozenset[str] = frozenset(configured)
        self._dev = is_dev
        # Frozen public list (preserve original case + order from settings)
        # used in the rejection payload so operators see exactly what's
        # whitelisted, not a lowercased+reordered version.
        self._allowed_display: tuple[str, ...] = tuple(settings.allowed_hosts)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        host_header = request.headers.get("host", "")
        host = self._strip_port(host_header).lower()
        if host and host not in self._allowed:
            # Log at INFO so the rejection is visible without curling
            # the response body. The hint mirrors what the JSON payload
            # below carries.
            logger.info(
                "z4j: rejected request - Host header %r is not in the "
                "allow-list. Allow it via `Z4J_ALLOWED_HOSTS=%s,...` or "
                "restart with `z4j serve --allowed-host %s`.",
                host_header,
                host,
                host,
            )
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_host",
                    "message": (
                        f"Host header {host!r} is not in the configured "
                        f"allow-list. The brain refuses unrecognized Host "
                        f"headers to prevent cache-poisoning attacks."
                    ),
                    "request_id": getattr(request.state, "request_id", None),
                    "details": {
                        "rejected_host": host,
                        "allowed_hosts": list(self._allowed_display),
                        "fix": (
                            f"Add the host to the allow-list. Either set "
                            f"Z4J_ALLOWED_HOSTS=\"{host},"
                            f"{','.join(self._allowed_display) or 'localhost'}\" "
                            f"in the brain's environment, OR restart with "
                            f"`z4j serve --allowed-host {host}` "
                            f"(repeatable). Then reload this page."
                        ),
                    },
                },
            )
        return await call_next(request)

    @staticmethod
    def _strip_port(host: str) -> str:
        """Strip the optional port suffix.

        Handles IPv6 forms (``[::1]:7700`` → ``[::1]``) and the
        plain ``host:port`` form. Returns the host unchanged if no
        port is present.
        """
        if host.startswith("["):
            end = host.find("]")
            if end == -1:
                return host
            return host[: end + 1]
        if ":" in host:
            return host.rsplit(":", 1)[0]
        return host


__all__ = ["HostValidationMiddleware"]
