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
            # Operator-facing log: ALWAYS verbose, regardless of mode.
            # The operator runs `z4j serve` and watches stderr (or
            # journalctl / docker logs); leaking detail there is fine.
            # Public HTTP responses below are NOT verbose in production.
            logger.info(
                "z4j: rejected request - Host header %r is not in the "
                "allow-list. Persist it via `z4j allowed-hosts add %s` "
                "or restart with `z4j serve --allowed-host %s`. Current "
                "allow-list: %s",
                host_header,
                host,
                host,
                list(self._allowed_display),
            )
            return self._build_rejection(request, host)
        return await call_next(request)

    def _build_rejection(self, request: Request, host: str) -> JSONResponse:
        """Build the 400 response body.

        Verbosity is gated on ``settings.environment == "dev"``:

        - **dev mode** (laptop, single-operator, the operator IS the
          HTTP client): include the rejected host, the full allow-list,
          and a concrete fix command. Helpful "what do I do" message.
        - **non-dev mode** (production, public-facing): minimal body -
          just the error code + request_id. Operators read the full
          detail from server logs (the INFO line above) which crawlers
          and attackers cannot see. Mirrors Django's DEBUG-only
          detailed-error pattern.

        This avoids leaking internal hostnames, LAN IPs, Tailscale node
        names, or a ready-to-paste env var value to anyone hitting the
        brain through a public reverse proxy.
        """
        request_id = getattr(request.state, "request_id", None)
        if self._dev:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_host",
                    "message": (
                        f"Host header {host!r} is not in the configured "
                        f"allow-list. The brain refuses unrecognized "
                        f"Host headers to prevent cache-poisoning "
                        f"attacks."
                    ),
                    "request_id": request_id,
                    "details": {
                        "rejected_host": host,
                        "allowed_hosts": list(self._allowed_display),
                        "fix": (
                            f"Persist the host: run "
                            f"`z4j allowed-hosts add {host}` and "
                            f"restart `z4j serve`. (Or pin via "
                            f"Z4J_ALLOWED_HOSTS env / "
                            f"`z4j serve --allowed-host {host}`.) "
                            f"Then reload this page."
                        ),
                    },
                },
            )
        # Production: opaque body. Operator correlates via request_id
        # against the verbose INFO log line above. Crawlers, scanners,
        # and attackers learn nothing about internal hostnames or the
        # configured allow-list.
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_host",
                "message": "Bad Request: invalid Host header.",
                "request_id": request_id,
            },
        )

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
