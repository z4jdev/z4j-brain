"""Brain configuration via :mod:`pydantic_settings`.

Twelve-factor: every value is sourced from an environment variable
prefixed ``Z4J_`` or, in development, from a ``.env`` file at the
process working directory. Missing required values cause startup to
fail fast with a Pydantic ``ValidationError``.

This module is intentionally framework-free below the FastAPI layer:
``Settings`` is just a frozen dataclass-like object passed into the
app factory. Tests construct their own ``Settings`` instance instead
of monkey-patching environment variables.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(ValueError):
    """Settings misconfiguration.

    Subclass of ``ValueError`` so callers can catch either, but with
    a distinct type so the brain operator-facing CLI can map this to
    a specific exit code. Raised by :meth:`Settings.model_post_init`
    when a cross-field invariant fails.

    Distinct from Pydantic's ``ValidationError`` because Pydantic
    serialises the original input dict in its error messages - and
    that input dict contains secrets we never want to land in stdout.
    """


class Settings(BaseSettings):
    """Resolved brain configuration.

    The brain refuses to start if any required value is missing or
    if a secret is shorter than 32 bytes. Operators see one clear
    Pydantic error at startup instead of obscure failures later.

    Attributes:
        database_url: Async SQLAlchemy URL,
            e.g. ``postgresql+asyncpg://user:pw@host/db``.
        secret: Master HMAC signing key. Used for command signatures
            and any HMAC-based identifier the brain mints. Must be
            at least 32 bytes.
        session_secret: Independent secret used to sign session
            cookies. Separate from ``secret`` so a session-cookie
            compromise does not extend to command signing.
        bind_host: ASGI bind host.
        bind_port: ASGI bind port.
        public_url: Externally reachable base URL of the brain.
            Used to build first-boot setup links and reverse-proxy
            redirect targets.
        cors_origins: Allowed CORS origins for the dashboard.
        log_level: stdlib logging level name.
        log_json: Emit logs as JSON when True, console-friendly when
            False (development).
        environment: Free-form environment label
            (``production``, ``staging``, ``dev``).
        event_retention_days: How long raw events live before
            partition pruning.
        audit_retention_days: How long audit-log rows live.
        command_timeout_seconds: Pending commands older than this
            are marked timed-out by the background worker.
        agent_offline_timeout_seconds: Heartbeats older than this
            mark the agent offline.
        ratelimit_commands_per_minute: Per-project upper bound for
            command issuance.
        ratelimit_events_per_second: Per-project upper bound for
            event ingestion.
        max_payload_size_bytes: Maximum REST request body size.
        max_ws_frame_bytes: Maximum inbound WebSocket frame size.
        metrics_enabled: Expose ``/metrics`` Prometheus scrape endpoint.
        session_duration_seconds: Lifetime of a dashboard session
            cookie.
        argon2_time_cost: argon2id time cost parameter.
        argon2_memory_cost: argon2id memory cost (KiB).
        argon2_parallelism: argon2id parallelism parameter.
        first_boot_token_ttl_seconds: How long the one-time setup
            token printed to stdout remains valid.
        dashboard_dist: Filesystem path to the built dashboard
            assets that will be mounted at ``/``.
    """

    model_config = SettingsConfigDict(
        env_prefix="Z4J_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    # ------------------------------------------------------------------
    # Required secrets - startup fails fast if missing or weak.
    # ------------------------------------------------------------------
    database_url: str = Field(
        ...,
        description="postgresql+asyncpg:// async SQLAlchemy URL",
    )
    secret: SecretStr = Field(
        ...,
        description="Master HMAC signing key (>=32 bytes)",
    )
    session_secret: SecretStr = Field(
        ...,
        description="Session cookie signing key (>=32 bytes)",
    )

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------
    bind_host: str = "0.0.0.0"
    bind_port: int = Field(default=7700, ge=1, le=65535)
    public_url: str = "http://localhost:7700"
    cors_origins: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Operational
    # ------------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json: bool = True
    environment: str = Field(default="production", max_length=40)

    # ------------------------------------------------------------------
    # Retention + worker cadence
    # ------------------------------------------------------------------
    event_retention_days: int = Field(default=30, ge=1, le=3650)
    audit_retention_days: int = Field(default=90, ge=1, le=3650)
    command_timeout_seconds: int = Field(default=60, ge=1, le=86_400)
    agent_offline_timeout_seconds: int = Field(default=30, ge=1, le=3600)
    #: Delete agent rows that have been offline for more than this
    #: many days. Keeps the Agents page tidy after removed
    #: containers. Set to 0 to disable pruning (useful for long
    #: audit retention windows; rely on the ``state=offline`` badge
    #: instead).
    agent_stale_prune_days: int = Field(default=30, ge=0, le=3650)

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------
    ratelimit_commands_per_minute: int = Field(default=100, ge=1)
    ratelimit_events_per_second: int = Field(default=10_000, ge=1)

    # ------------------------------------------------------------------
    # Safety limits
    # ------------------------------------------------------------------
    max_payload_size_bytes: int = Field(default=8_192, ge=128)
    max_ws_frame_bytes: int = Field(default=1_048_576, ge=1024)

    # ------------------------------------------------------------------
    # Registry (asyncpg LISTEN/NOTIFY)
    # ------------------------------------------------------------------
    asyncpg_connect_timeout: float = Field(default=10.0, ge=1.0, le=60.0)
    asyncpg_close_timeout: float = Field(default=5.0, ge=1.0, le=30.0)

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------
    metrics_enabled: bool = True

    # ------------------------------------------------------------------
    # Auth - passwords
    # ------------------------------------------------------------------
    argon2_time_cost: int = Field(default=3, ge=1, le=10)
    argon2_memory_cost: int = Field(default=65_536, ge=8192)
    argon2_parallelism: int = Field(default=4, ge=1, le=16)
    password_min_length: int = Field(default=8, ge=8, le=128)

    # ------------------------------------------------------------------
    # Auth - sessions (server-side, revocable)
    # ------------------------------------------------------------------
    #: Hard cap on a single session's lifetime, regardless of activity.
    #: After this elapses since ``issued_at`` the session is rejected
    #: even if the user has been active. Default: 7 days.
    session_absolute_lifetime_seconds: int = Field(default=604_800, ge=60)
    #: Sliding idle timeout. If ``last_seen_at`` is older than this,
    #: the session is rejected even if the absolute lifetime has not
    #: elapsed. Default: 30 minutes.
    session_idle_timeout_seconds: int = Field(default=1_800, ge=60)
    #: When True, the resolved client user-agent at session-issue time
    #: is enforced on every subsequent request - change of UA voids
    #: the session. Default OFF: too many false positives on mobile
    #: networks and behind corporate proxies.
    session_pin_user_agent: bool = False

    #: SameSite attribute on the session cookie.
    #:
    #: - ``"lax"`` (default): cookie IS sent on top-level GET
    #:   navigation, preserving the UX where a link in an email
    #:   that opens https://z4j.example.com/projects/... lands on
    #:   the project page rather than the login page. Already
    #:   blocks every cross-site form-POST and every cross-site
    #:   image/iframe request, which covers the standard CSRF
    #:   threat model. Combined with the double-submit CSRF token
    #:   (``require_csrf``) this is the best UX/security trade for
    #:   the typical operator dashboard.
    #: - ``"strict"``: cookie is NEVER sent on cross-site navigation
    #:   of any kind. Stronger defense in depth - useful for
    #:   security-paranoid deployments where operators always start
    #:   from a bookmark/typed URL. Costs the email-link UX above.
    #: - ``"none"`` is intentionally NOT supported. SameSite=None
    #:   requires Secure AND opens cross-site state-changing
    #:   requests; we never want it for a session cookie.
    session_cookie_samesite: str = Field(
        default="lax",
        pattern="^(lax|strict)$",
    )

    # ------------------------------------------------------------------
    # Auth - login lockout + backoff
    # ------------------------------------------------------------------
    login_lockout_threshold: int = Field(default=10, ge=3, le=100)
    login_lockout_duration_seconds: int = Field(default=900, ge=60, le=86_400)
    login_backoff_base_seconds: float = Field(default=0.5, ge=0.0, le=10.0)
    login_backoff_max_seconds: float = Field(default=5.0, ge=0.0, le=60.0)
    #: Minimum total response time for ``/auth/login`` (success OR
    #: failure). Held by ``await asyncio.sleep`` so DB-query, argon2,
    #: and cache-hit/miss variance cannot be exploited as a timing
    #: oracle for username enumeration.
    login_min_duration_ms: int = Field(default=300, ge=0, le=2000)
    #: When True, structured logs at ``z4j.brain.auth`` carry the
    #: attempted email on failed login. Always recorded in the audit
    #: log row regardless. Default OFF - emails in stdout logs are a
    #: PII liability for shipped log streams.
    log_login_email: bool = False

    # ------------------------------------------------------------------
    # First-boot
    # ------------------------------------------------------------------
    first_boot_token_ttl_seconds: int = Field(default=900, ge=60, le=86_400)
    #: Hard cap on setup-token verification attempts per IP per 15
    #: minutes. Defends the setup endpoint against brute-forcing the
    #: 256-bit token in the (unlikely) window between mint and consume.
    # Sliding-window cap on FAILED setup attempts per IP. Bumped from
    # 5 to 30 in 1.0.4 because the original threshold tripped on common
    # operator UX patterns (form validation typos, stale browser tabs
    # from prior server runs, double-submits). The window is still 15
    # minutes; the 30 ceiling protects against credential stuffing
    # while leaving room for honest retries.
    first_boot_attempts_per_ip: int = Field(default=30, ge=1, le=100)

    # ------------------------------------------------------------------
    # Network - host + proxy + body + timeouts
    # ------------------------------------------------------------------
    #: Allowed Host headers. Production must populate this. Empty
    #: list in dev defaults to ``["localhost", "127.0.0.1"]`` via the
    #: model validator below.
    allowed_hosts: list[str] = Field(default_factory=list)
    #: CIDR list of reverse-proxy IPs whose ``X-Forwarded-For`` we
    #: trust. Empty list = trust no proxies (audit logs use the raw
    #: socket peer address). Defaults are dev-only.
    trusted_proxies: list[str] = Field(default_factory=list)
    #: Per-request handler wall-clock budget. Handlers exceeding this
    #: are cancelled and the response becomes a 504 + audit row.
    request_timeout_seconds: int = Field(default=30, ge=1, le=600)
    #: Strict-Transport-Security max-age. Only emitted when
    #: ``environment="production"`` AND ``public_url`` starts with
    #: ``https://``.
    hsts_max_age_seconds: int = Field(default=31_536_000, ge=0)
    #: Whether to append ``includeSubDomains`` to the HSTS header.
    #: Defaults to True (the safer choice for a brain deployed at
    #: a dedicated subdomain), but operators serving HTTP siblings
    #: under the same parent domain MUST set this to False or HSTS
    #: will break those siblings irreversibly for the cache window.
    hsts_include_subdomains: bool = Field(default=True)

    # ------------------------------------------------------------------
    # Database safety
    # ------------------------------------------------------------------
    db_statement_timeout_ms: int = Field(default=10_000, ge=100, le=600_000)
    db_lock_timeout_ms: int = Field(default=3_000, ge=100, le=600_000)
    db_idle_in_tx_timeout_ms: int = Field(default=30_000, ge=100, le=600_000)
    #: When True, refuse a Postgres URL that disables SSL
    #: (``sslmode=disable`` or no sslmode at all). Auto-relaxed when
    #: ``environment="dev"``.
    require_db_ssl: bool = True

    # ------------------------------------------------------------------
    # CORS hardening
    # ------------------------------------------------------------------
    cors_allow_credentials: bool = True

    # ------------------------------------------------------------------
    # Agent gateway (B4)
    # ------------------------------------------------------------------
    #: Which BrainRegistry implementation to wire at startup.
    #: Production must use ``postgres_notify``. ``local`` is the
    #: in-process map used by unit tests for speed - it does NOT
    #: route across worker processes.
    registry_backend: Literal["postgres_notify", "local"] = "postgres_notify"
    #: Heartbeat self-NOTIFY interval. The listener task NOTIFYs on
    #: a dedicated channel every N seconds and a watchdog kills the
    #: connection if its own message has not round-tripped within
    #: ``registry_listener_heartbeat_timeout_seconds``. Defends
    #: against the queue-lock failure mode.
    registry_listener_heartbeat_seconds: int = Field(default=10, ge=1, le=300)
    registry_listener_heartbeat_timeout_seconds: int = Field(
        default=25, ge=2, le=600,
    )
    #: Hard recycle interval for the listener connection. Belt-and-
    #: braces against silent NAT/proxy wedges and hung backends.
    registry_listener_max_age_seconds: int = Field(default=900, ge=60, le=86_400)
    #: Periodic poll interval for "pending commands targeting an
    #: agent I currently hold". Recovers from any notify that was
    #: lost or delivered while the listener was reconnecting.
    #: Lower bound is 1s so integration tests can drive a fast
    #: sweep; production deployments should leave the default of 30s.
    registry_reconcile_interval_seconds: int = Field(default=30, ge=1, le=600)
    #: Maximum inbound WebSocket frame size from agents. Frames
    #: larger than this kill the connection. 1 MiB is well above
    #: any legitimate event_batch shape.
    ws_max_frame_bytes: int = Field(default=1_048_576, ge=8192, le=33_554_432)
    #: Maximum number of WebSocket connections we accept for one
    #: agent_id at the same time. Always 1 in v1 - a second
    #: connection from the same agent kills the first one.
    ws_per_agent_concurrency_limit: int = Field(default=1, ge=1, le=4)
    #: Per-connection idle timeout for both ``/ws/agent`` and
    #: ``/ws/dashboard``. If no frame arrives in this many seconds
    #: the connection is closed and the file descriptor released.
    #:
    #: For agents this MUST be larger than the heartbeat interval
    #: declared in the ``hello_ack`` frame (10s by default) - a
    #: well-behaved agent sends a heartbeat every 10s, so 60s gives
    #: 6 missed heartbeats of headroom before we kill the socket.
    #:
    #: For dashboards the client sends a ping every 25s; 90s gives
    #: 3 missed pings of headroom which is plenty for normal
    #: network jitter and tab-throttling on backgrounded tabs.
    ws_idle_timeout_seconds: int = Field(default=90, ge=15, le=3600)
    #: Background worker poll intervals.
    command_timeout_sweep_seconds: int = Field(default=5, ge=1, le=300)
    agent_health_sweep_seconds: int = Field(default=10, ge=1, le=300)
    #: Cadence for :class:`AgentHygieneWorker`. Once a day is
    #: enough; the prune target is "weeks stale", not "minutes".
    agent_hygiene_sweep_seconds: int = Field(
        default=86_400, ge=60, le=604_800,
    )
    #: Cadence for :class:`ReconciliationWorker`. Every 5 min is a
    #: good compromise between prompt stuck-task resolution and
    #: per-agent WebSocket load.
    reconciliation_sweep_seconds: int = Field(default=300, ge=30, le=3600)
    #: Age after which a non-terminal task is considered "stuck" and
    #: eligible for result-backend reconciliation. 15 min tolerates
    #: long tasks without scheduling a reconcile for every in-flight
    #: retry.
    reconciliation_stale_threshold_seconds: int = Field(
        default=900, ge=60, le=86_400,
    )
    #: Default per-page cap on REST list endpoints.
    rest_default_page_size: int = Field(default=50, ge=1, le=1000)
    rest_max_page_size: int = Field(default=500, ge=1, le=5000)

    # ------------------------------------------------------------------
    # Dashboard assets
    # ------------------------------------------------------------------
    dashboard_dist: str = "/app/dashboard/dist"

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @model_validator(mode="before")
    @classmethod
    def _enforce_min_secret_length(cls, data: Any) -> Any:
        """Reject secrets shorter than 32 bytes BEFORE Pydantic sees them.

        Runs in ``mode="before"`` so we never let the raw secret
        value reach Pydantic's field-level validator - Pydantic
        echoes ``input_value`` into its ValidationError messages,
        which would put a (presumably weak but still confidential)
        secret into the brain operator's stdout. We raise our own
        ValueError that names only the field.
        """
        if not isinstance(data, dict):
            return data
        for field_name in ("secret", "session_secret"):
            raw = data.get(field_name)
            if raw is None:
                continue
            value = (
                raw.get_secret_value()
                if isinstance(raw, SecretStr)
                else str(raw)
            )
            if len(value.encode("utf-8")) < 32:
                raise ValueError(
                    f"{field_name} must be at least 32 bytes long",
                )
        return data

    @model_validator(mode="before")
    @classmethod
    def _coerce_registry_backend_for_sqlite(cls, data: Any) -> Any:
        """Force ``registry_backend=local`` when the DB is SQLite.

        SQLite has no ``LISTEN/NOTIFY`` primitive, so
        ``postgres_notify`` is structurally impossible on a SQLite
        URL - the only sane backend is the in-process ``local`` hub.

        Coercing here (rather than failing loudly) lets
        ``pip install z4j-brain && z4j-brain serve`` work out of the
        box against the default SQLite DB without the operator having
        to also set ``Z4J_REGISTRY_BACKEND=local`` AND install the
        ``[postgres]`` extra just to get past the unconditional
        ``import asyncpg`` inside ``postgres_notify.py``.
        """
        if not isinstance(data, dict):
            return data
        db_url = data.get("database_url") or ""
        if isinstance(db_url, str) and db_url.startswith("sqlite"):
            data["registry_backend"] = "local"
        return data

    def __init__(self, **values: Any) -> None:
        """Construct, validate, then run cross-field security checks.

        We deliberately do the security checks OUTSIDE Pydantic's
        validator pipeline. Pydantic's ``ValidationError`` always
        serialises ``input_value`` in its message - and for a
        Settings object that contains secrets, that means one
        misconfiguration would dump every value (including the
        secrets) into stdout. By calling ``super().__init__`` first
        and then doing the security checks ourselves, any
        :class:`ConfigError` we raise has a message we fully control
        and never includes the input dict.
        """
        super().__init__(**values)
        self._enforce_security_invariants()

    def _enforce_security_invariants(self) -> None:
        """Cross-field security checks. See :meth:`__init__`."""
        is_dev = self.environment == "dev"

        # CORS: never wildcard with credentials.
        if self.cors_allow_credentials and "*" in self.cors_origins:
            raise ConfigError(
                "cors_origins must not contain '*' when "
                "cors_allow_credentials is True",
            )

        # Production: allowed_hosts must be explicit.
        if not is_dev and not self.allowed_hosts:
            raise ConfigError(
                "allowed_hosts must be set in non-dev environments "
                "(host header is not validated otherwise)",
            )

        # Production: public_url should be https.
        if not is_dev and not self.public_url.startswith("https://"):
            raise ConfigError(
                "public_url must use https:// in non-dev environments",
            )

        # Audit A6: strict validation of public_url content. Newlines,
        # embedded userinfo (``user@host``), and non-http(s) schemes
        # are rejected at settings-load time so the value used to
        # build invitation + password-reset links can never be an
        # attacker-controlled string that redirects users elsewhere.
        if any(ch in self.public_url for ch in ("\r", "\n", " ", "\t")):
            raise ConfigError(
                "public_url must not contain whitespace or newlines",
            )
        if "@" in self.public_url.split("://", 1)[-1].split("/", 1)[0]:
            raise ConfigError(
                "public_url must not contain embedded userinfo (user@host); "
                "that shape would redirect users to attacker-controlled "
                "hosts when emailed",
            )
        if not (
            self.public_url.startswith("http://")
            or self.public_url.startswith("https://")
        ):
            raise ConfigError(
                "public_url must start with http:// or https://",
            )

        # Database: require SSL for production Postgres URLs.
        if (
            self.require_db_ssl
            and not is_dev
            and self.database_url.startswith("postgresql+asyncpg://")
        ):
            url_lower = self.database_url.lower()
            if "sslmode=disable" in url_lower:
                raise ConfigError(
                    "database_url has sslmode=disable which is not "
                    "permitted when require_db_ssl is True",
                )
            if "sslmode=" not in url_lower:
                raise ConfigError(
                    "database_url must include sslmode=require (or "
                    "stricter) when require_db_ssl is True",
                )



    @field_validator("database_url")
    @classmethod
    def _enforce_async_driver(cls, v: str) -> str:
        """Database URL must use the asyncpg driver.

        SQLAlchemy will silently fall back to a sync driver if a
        plain ``postgresql://`` URL is supplied, which then explodes
        at the first ``await session.execute(...)`` call with an
        unhelpful traceback. Catch it here.
        """
        if not (
            v.startswith("postgresql+asyncpg://")
            or v.startswith("sqlite+aiosqlite://")
        ):
            raise ValueError(
                "database_url must use postgresql+asyncpg:// "
                "(or sqlite+aiosqlite:// for tests)",
            )
        return v


__all__ = ["ConfigError", "Settings"]
