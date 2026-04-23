"""``z4j-brain`` command-line entry point.

Subcommands:

- ``z4j-brain serve`` - run uvicorn against ``create_app``
- ``z4j-brain migrate upgrade [revision]`` - alembic upgrade
- ``z4j-brain migrate downgrade <revision>`` - alembic downgrade
- ``z4j-brain migrate revision -m "msg"`` - generate a new migration
- ``z4j-brain version`` - print the version

The CLI is intentionally tiny - operators run uvicorn directly in
production. This entry point exists so contributors do not need to
remember the uvicorn invocation flags.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from z4j_brain import __version__


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point installed as the ``z4j`` (and back-compat ``z4j-brain``)
    console script.
    """
    # Auto-detect the prog name from how the user invoked us. When
    # they typed ``z4j --help`` we want examples to read ``z4j ...``;
    # when they typed ``z4j-brain --help`` we want ``z4j-brain ...``.
    # Falls back to ``z4j`` (the canonical name) if argv[0] is the
    # python -m form or anything we can't parse.
    invoked = Path(sys.argv[0]).stem if sys.argv and sys.argv[0] else "z4j"
    prog = invoked if invoked in {"z4j", "z4j-brain"} else "z4j"

    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "z4j brain server (AGPL v3) - operator CLI.\n"
            "\n"
            "Common flows:\n"
            f"  {prog} serve                     # start the dashboard + API\n"
            f"  {prog} check                     # validate config + DB\n"
            f"  {prog} status                    # current-state summary\n"
            f"  {prog} createsuperuser ...       # create the first admin\n"
            f"  {prog} changepassword <email>    # reset a user's password\n"
            f"  {prog} reset [--all]             # nuke DB state\n"
            f"  {prog} migrate upgrade head      # run alembic migrations\n"
            f"  {prog} audit verify              # verify audit-log HMAC chain\n"
            f"  {prog} version                   # print installed version\n"
            "\n"
            f"Run `{prog} <command> --help` for per-command flags."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(
        dest="command",
        required=False,
        title="commands",
        metavar="<command>",
    )

    # serve
    serve = sub.add_parser("serve", help="run uvicorn against create_app")
    serve.add_argument("--host", default=None, help="bind host")
    serve.add_argument("--port", type=int, default=None, help="bind port")
    serve.add_argument("--workers", type=int, default=1)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument(
        "--admin-email",
        default=None,
        help="auto-create admin user on first boot (skips setup wizard)",
    )
    serve.add_argument(
        "--admin-password",
        default=None,
        help="password for the auto-created admin user",
    )

    # migrate
    migrate = sub.add_parser("migrate", help="run an alembic command")
    migrate.add_argument(
        "action",
        choices=("upgrade", "downgrade", "revision", "current", "history"),
    )
    migrate.add_argument("rest", nargs=argparse.REMAINDER)

    # audit
    audit = sub.add_parser("audit", help="audit-log subcommands")
    audit_sub = audit.add_subparsers(dest="audit_command", required=True)
    audit_verify = audit_sub.add_parser(
        "verify",
        help="verify the per-row HMAC for every audit_log entry",
    )
    audit_verify.add_argument(
        "--limit",
        type=int,
        default=5_000,
        help=(
            "maximum number of rows to verify per invocation "
            "(default: 5000, hard cap: 5000). The repository's "
            "stream_for_verify returns up to N rows from the "
            "oldest entry; re-run for more if your audit log "
            "is larger."
        ),
    )

    # bootstrap-admin: imperative first-boot admin creation.
    # Complements the Z4J_BOOTSTRAP_ADMIN_* env var path so operators
    # who prefer a CLI step (or want to re-create an admin after
    # losing credentials via DB reset) have one.
    bootstrap = sub.add_parser(
        "bootstrap-admin",
        help="create the initial admin user + default project (first-boot only)",
    )
    bootstrap.add_argument(
        "--email", required=True, help="admin email address",
    )
    bootstrap.add_argument(
        "--display-name", default=None, help="optional display name",
    )
    bootstrap_pw = bootstrap.add_mutually_exclusive_group(required=True)
    bootstrap_pw.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from stdin (recommended; no shell history leak)",
    )
    bootstrap_pw.add_argument(
        "--password",
        default=None,
        help="password on the command line (NOT recommended - visible in ps/history)",
    )

    # reset-setup (narrow: only pending tokens + setup.* audit rows;
    # refuses if admin exists). The broader `reset` below wipes
    # everything including the admin. Both exist because they solve
    # different problems.
    reset_setup = sub.add_parser(
        "reset-setup",
        help=(
            "wipe pending first-boot tokens + recent setup audit rows. "
            "REFUSES if an admin user already exists."
        ),
    )
    reset_setup.add_argument(
        "--force",
        action="store_true",
        help="proceed without the safety prompt (for scripts)",
    )

    # reset (destructive; full DB wipe)
    reset = sub.add_parser(
        "reset",
        help=(
            "wipe every runtime table (users, sessions, projects, "
            "agents, tasks, events, schedules, audit, ...). Schema "
            "stays; alembic doesn't re-run. Pre-first-boot state "
            "after this."
        ),
        description=(
            "Wipe every runtime table and put the brain back into "
            "pre-first-boot state. After this command, the next "
            "`serve` mints a fresh setup token and prints a new "
            "one-time admin-creation URL.\n"
            "\n"
            "Does NOT touch:\n"
            "  - the alembic schema (run `migrate downgrade base` for that)\n"
            "  - ~/.z4j/secret.env (unless --nuke-secrets)\n"
            "  - ~/.z4j/z4j.db file (rows only, not the file itself)\n"
            "\n"
            "REQUIRES --force to proceed. Destructive. Irrecoverable."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    reset.add_argument(
        "--force",
        action="store_true",
        help="proceed without the safety prompt (required to wipe)",
    )
    reset.add_argument(
        "--nuke-secrets",
        action="store_true",
        help=(
            "also delete ~/.z4j/secret.env so the next serve mints "
            "fresh HMAC keys. Any existing session cookies + agent "
            "tokens become invalid."
        ),
    )

    # createsuperuser (alias to bootstrap-admin; Django-familiar name)
    createsuperuser = sub.add_parser(
        "createsuperuser",
        help="create an admin user (Django-style alias for bootstrap-admin)",
    )
    createsuperuser.add_argument(
        "--email", required=True, help="admin email address",
    )
    createsuperuser.add_argument(
        "--display-name", default=None, help="optional display name",
    )
    createsuperuser_pw = createsuperuser.add_mutually_exclusive_group(
        required=True,
    )
    createsuperuser_pw.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from stdin (recommended)",
    )
    createsuperuser_pw.add_argument(
        "--password",
        default=None,
        help="password on the command line (NOT recommended)",
    )

    # changepassword
    changepassword = sub.add_parser(
        "changepassword",
        help="change a user's password (admin recovery / CLI-only ops)",
    )
    changepassword.add_argument("email", help="email of the user to reset")
    changepassword_pw = changepassword.add_mutually_exclusive_group(
        required=True,
    )
    changepassword_pw.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from stdin (recommended)",
    )
    changepassword_pw.add_argument(
        "--password",
        default=None,
        help="password on the command line (NOT recommended)",
    )

    # check
    sub.add_parser(
        "check",
        help=(
            "validate config + DB connectivity + that alembic is "
            "at head. Non-destructive. Exit 0 = healthy."
        ),
    )

    # status
    sub.add_parser(
        "status",
        help=(
            "print a summary of current brain state: user count, "
            "project count, agent count, recent task activity."
        ),
    )

    # version
    sub.add_parser("version", help="print installed z4j-brain version")

    args = parser.parse_args(argv)

    if args.command == "version" or args.command is None:
        print(__version__)  # noqa: T201
        return 0

    if args.command == "serve":
        return _run_serve(args)

    if args.command == "migrate":
        return _run_migrate(args)

    if args.command == "audit":
        return _run_audit(args)

    if args.command == "bootstrap-admin":
        return _run_bootstrap_admin(args)

    if args.command == "reset-setup":
        return _run_reset_setup(args)

    if args.command == "reset":
        return _run_reset(args)

    if args.command == "createsuperuser":
        # Identical shape to bootstrap-admin; dispatch through the
        # same implementation to keep one code path.
        return _run_bootstrap_admin(args)

    if args.command == "changepassword":
        return _run_changepassword(args)

    if args.command == "check":
        return _run_check(args)

    if args.command == "status":
        return _run_status(args)

    parser.error(f"unknown command {args.command!r}")
    return 2


def _run_serve(args: argparse.Namespace) -> int:
    """Run uvicorn programmatically.

    We import uvicorn lazily so ``z4j-brain version`` and
    ``z4j-brain migrate`` do not pay the uvicorn import cost.
    """
    import os

    import uvicorn

    # Auto-setup: pass admin credentials as env vars so the brain's
    # first-boot hook creates the admin user + default project and
    # skips the setup-URL banner. The env var names below are the
    # same names a Helm / compose manifest sets directly, so
    # ``z4j-brain serve --admin-email ...`` and native env-var
    # provisioning share one code path.
    if args.admin_email:
        os.environ["Z4J_BOOTSTRAP_ADMIN_EMAIL"] = args.admin_email
    if args.admin_password:
        os.environ["Z4J_BOOTSTRAP_ADMIN_PASSWORD"] = args.admin_password

    # Default to SQLite if no DATABASE_URL is set (bare-metal mode).
    if not os.environ.get("Z4J_DATABASE_URL"):
        data_dir = Path.home() / ".z4j"
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "z4j.db"
        os.environ["Z4J_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
        os.environ.setdefault("Z4J_REGISTRY_BACKEND", "local")
        print(  # noqa: T201
            f"z4j-brain: using SQLite at {db_path} "
            "(set Z4J_DATABASE_URL for Postgres)",
        )

    # Auto-bootstrap HMAC secrets so ``pip install z4j-brain && z4j-brain
    # serve`` works zero-config on a fresh machine. Mirrors the Docker
    # image entrypoint behavior. Persisted to ``~/.z4j/secret.env`` so
    # tokens, sessions, and the audit-log HMAC chain survive across
    # restarts.
    #
    # Precedence:
    #   1. explicit env (operator-provided) -> use as-is
    #   2. ``~/.z4j/secret.env`` exists from a previous boot -> source
    #   3. neither -> mint fresh + persist to ``~/.z4j/secret.env``
    #
    # In production the operator must set Z4J_SECRET + Z4J_SESSION_SECRET
    # explicitly (case 1). Auto-mint is for dev / homelab / evaluation.
    if not os.environ.get("Z4J_SECRET"):
        data_dir = Path.home() / ".z4j"
        data_dir.mkdir(parents=True, exist_ok=True)
        secret_env = data_dir / "secret.env"
        if secret_env.exists():
            for line in secret_env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
            print(  # noqa: T201
                f"z4j-brain: loaded persisted Z4J_SECRET from {secret_env}",
            )
        else:
            import secrets as _secrets  # local alias to avoid shadowing

            # Detect a stale DB from a prior install whose secret.env
            # is gone. This happens when the operator did pip install
            # of an older z4j-brain version that crashed mid-bootstrap
            # (so the DB got created during alembic upgrade BUT the
            # secret was never minted), then upgraded to a fixed
            # version. Without this guard, we mint a fresh secret + a
            # fresh first-boot token, but the DB already has the
            # alembic schema PLUS audit-log rows signed under the old
            # (lost) secret. Any future audit-log verification would
            # fail, AND the operator gets confusing "invalid_token"
            # errors because their browser may have a stale URL from
            # the prior crashed run.
            #
            # When secret.env is brand new, the safe default is to
            # also wipe z4j.db so we start truly fresh. The user
            # already explicitly asked for fresh state by deleting
            # (or never creating) secret.env. Backed up to .bak so
            # they can recover if they did this by mistake.
            stale_db = data_dir / "z4j.db"
            if stale_db.exists():
                backup = stale_db.with_suffix(".db.stale-bak")
                # Replace any prior bak so we don't accumulate them
                if backup.exists():
                    backup.unlink()
                stale_db.rename(backup)
                # Also nuke SQLite's WAL + journal sidecars
                for suffix in (".db-wal", ".db-shm", ".db-journal"):
                    sidecar = data_dir / f"z4j{suffix}"
                    if sidecar.exists():
                        sidecar.unlink()
                print(  # noqa: T201
                    f"z4j-brain: found stale {stale_db.name} from a prior "
                    f"install but no secret.env - moved aside to "
                    f"{backup.name} so this install starts fresh. "
                    "Delete the .stale-bak when you no longer need it.",
                )

            new_secret = _secrets.token_urlsafe(48)
            new_session = _secrets.token_urlsafe(48)
            secret_env.write_text(
                f"Z4J_SECRET={new_secret}\nZ4J_SESSION_SECRET={new_session}\n",
                encoding="utf-8",
            )
            try:
                # chmod 600 on Unix; no-op on Windows (NTFS uses ACLs).
                # Best-effort: we don't fail the boot if chmod is denied.
                secret_env.chmod(0o600)
            except OSError:
                pass
            os.environ["Z4J_SECRET"] = new_secret
            os.environ["Z4J_SESSION_SECRET"] = new_session
            print(  # noqa: T201
                f"z4j-brain: minted fresh Z4J_SECRET + Z4J_SESSION_SECRET, "
                f"persisted to {secret_env}",
            )
            print(  # noqa: T201
                "z4j-brain: WARNING - evaluation mode. For production, set "
                "Z4J_SECRET + Z4J_SESSION_SECRET explicitly via env vars and "
                "back up the secret store.",
            )

    # In dev mode the brain's settings validators expect localhost-friendly
    # values for allowed_hosts + a non-https public_url. Set sane defaults
    # if the operator hasn't pinned them. Mirrors the Docker entrypoint.
    if not os.environ.get("Z4J_DATABASE_URL", "").startswith("postgresql"):
        os.environ.setdefault("Z4J_ENVIRONMENT", "dev")
        os.environ.setdefault(
            "Z4J_ALLOWED_HOSTS", '["localhost","127.0.0.1"]',
        )

    # Auto-migrate before serve. The bare-metal quickstart used to
    # leave migrations to a manual ``z4j-brain migrate upgrade head``
    # step - easy to forget, and the first request would then blow
    # up with an opaque "relation does not exist" error.
    # Running ``alembic upgrade head`` here is idempotent (no-op
    # when already at head) and fails fast with a clear error if
    # the DB is unreachable. Can be disabled for managed-migration
    # deployments by setting ``Z4J_AUTO_MIGRATE=false`` (Helm /
    # GitOps workflows that want migrations as a separate Job).
    if os.environ.get("Z4J_AUTO_MIGRATE", "true").lower() != "false":
        try:
            _auto_migrate()
        except SystemExit as exc:
            # alembic_main exits on error - translate to a clear
            # message + non-zero return so the operator doesn't
            # have to read a cryptic argparse trace.
            print(  # noqa: T201
                f"z4j-brain: auto-migrate failed (code {exc.code}). "
                "Set Z4J_AUTO_MIGRATE=false and run `z4j-brain "
                "migrate upgrade head` manually if you are managing "
                "migrations separately.",
            )
            return 1

    from z4j_brain.settings import Settings

    settings = Settings()  # type: ignore[call-arg]
    uvicorn.run(
        "z4j_brain.main:create_app",
        host=args.host or settings.bind_host,
        port=args.port or settings.bind_port,
        factory=True,
        workers=args.workers,
        reload=args.reload,
        log_config=None,  # we configure structlog ourselves
    )
    return 0


def _run_migrate(args: argparse.Namespace) -> int:
    """Delegate to alembic with the brain's bundled config.

    Resolution order for ``alembic.ini``:

    1. ``$Z4J_ALEMBIC_INI`` if set (used by the docker image to point
       at ``/app/alembic.ini``).
    2. ``./alembic.ini`` in the current working directory (the
       contributor flow when running from the source tree).
    3. The source-tree location next to ``backend/src/`` (legacy
       fallback for editable installs).
    """
    import os
    from alembic.config import main as alembic_main

    # Bootstrap env (DB URL + secrets) so alembic's env.py can
    # instantiate Settings(). Fresh installs don't have these yet.
    _bootstrap_env_for_management_commands()

    candidates: list[Path] = []
    env_path = os.environ.get("Z4J_ALEMBIC_INI")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path.cwd() / "alembic.ini")
    # pip-install path: alembic.ini is bundled inside the installed
    # package right next to cli.py. This is THE case we care about
    # for ``pip install z4j-brain && z4j-brain serve``.
    candidates.append(Path(__file__).resolve().parent / "alembic.ini")
    # Editable / source-tree fallback: when developing inside the
    # monorepo the ini lives at packages/z4j-brain/backend/alembic.ini
    # i.e. three parents up from this file.
    candidates.append(
        Path(__file__).resolve().parent.parent.parent / "alembic.ini",
    )

    config_path = next((p for p in candidates if p.exists()), None)
    if config_path is None:
        print(  # noqa: T201
            "z4j-brain: alembic.ini not found in any of: "
            + ", ".join(str(p) for p in candidates),
            file=sys.stderr,
        )
        return 2

    cli_args = ["-c", str(config_path), args.action, *args.rest]
    alembic_main(argv=cli_args, prog="z4j-brain migrate")
    return 0


def _auto_migrate() -> None:
    """Run ``alembic upgrade head`` against the configured DB.

    Called by :func:`_run_serve` on bare-metal starts so the
    quickstart is one-command: ``z4j-brain serve`` → working
    brain. Idempotent; a no-op when the DB is already at head.
    Raises ``SystemExit`` on alembic failure (propagated to the
    serve caller which renders a friendly error).
    """
    import os
    from alembic.config import main as alembic_main

    candidates: list[Path] = []
    env_path = os.environ.get("Z4J_ALEMBIC_INI")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path.cwd() / "alembic.ini")
    # pip-install path: alembic.ini is bundled inside the installed
    # package right next to cli.py. This is THE case we care about
    # for ``pip install z4j-brain && z4j-brain serve``.
    candidates.append(Path(__file__).resolve().parent / "alembic.ini")
    # Editable / source-tree fallback: when developing inside the
    # monorepo the ini lives at packages/z4j-brain/backend/alembic.ini
    # i.e. three parents up from this file.
    candidates.append(
        Path(__file__).resolve().parent.parent.parent / "alembic.ini",
    )
    config_path = next((p for p in candidates if p.exists()), None)
    if config_path is None:
        print(  # noqa: T201
            "z4j-brain: auto-migrate skipped (alembic.ini not found); "
            "set Z4J_ALEMBIC_INI or run from the source tree.",
        )
        return
    alembic_main(
        argv=["-c", str(config_path), "upgrade", "head"],
        prog="z4j-brain migrate (auto)",
    )


def _run_audit(args: argparse.Namespace) -> int:
    """Dispatch ``z4j-brain audit <subcommand>``."""
    if args.audit_command == "verify":
        return _run_audit_verify(args)
    print(  # noqa: T201
        f"z4j-brain audit: unknown subcommand {args.audit_command!r}",
        file=sys.stderr,
    )
    return 2


def _run_audit_verify(args: argparse.Namespace) -> int:
    """Stream the audit log and report any HMAC mismatches.

    Returns 0 on clean verification, 1 on at least one mismatch,
    2 on configuration / connection failure. Operators wire this
    into a nightly check and page on non-zero exit.
    """
    import asyncio

    # Bootstrap env so fresh / bare-metal installs don't crash with
    # a Settings ValidationError before we even open the DB.
    _bootstrap_env_for_management_commands()

    from z4j_brain.domain.audit_service import AuditService
    from z4j_brain.persistence.database import (
        DatabaseManager,
        create_engine_from_settings,
    )
    from z4j_brain.persistence.repositories import AuditLogRepository
    from z4j_brain.settings import Settings

    try:
        settings = Settings()  # type: ignore[call-arg]
    except Exception as exc:  # noqa: BLE001
        print(  # noqa: T201
            f"z4j-brain audit verify: failed to load settings: "
            f"{type(exc).__name__}",
            file=sys.stderr,
        )
        return 2

    audit = AuditService(settings)

    async def _run() -> int:
        engine = create_engine_from_settings(settings)
        db = DatabaseManager(engine)
        verified = 0
        mismatches: list[str] = []
        try:
            async with db.session() as session:
                repo = AuditLogRepository(session)
                rows = await repo.stream_for_verify(chunk=args.limit)
                for row in rows:
                    if audit.verify_row(row):
                        verified += 1
                    else:
                        mismatches.append(str(row.id))
        finally:
            await db.dispose()
        print(f"verified: {verified}")  # noqa: T201
        if mismatches:
            print(f"MISMATCHES ({len(mismatches)}):")  # noqa: T201
            for mid in mismatches:
                print(f"  {mid}")  # noqa: T201
            return 1
        return 0

    return asyncio.run(_run())


def _run_reset_setup(args: argparse.Namespace) -> int:
    """Wipe pending first-boot tokens and the recent setup audit-log
    rows so the next ``serve`` mints a fresh token from a clean slate.

    Refuses if a first admin already exists (that's a security hole,
    not a recovery path - someone is trying to reset onboarding for
    a configured brain). Use the dashboard's account-recovery flow
    or restore from backup instead.

    Use case: operator restarted the brain, the browser still has a
    stale URL with the old token, every retry is failing with
    "invalid_token", and the per-IP rate limit triggered a 15-minute
    lockout. Run this command, then restart the brain.
    """
    import asyncio
    import sys
    from pathlib import Path

    # Refuse early if there's no DB to reset (pre-first-boot state).
    db_path = Path.home() / ".z4j" / "z4j.db"
    if not db_path.exists():
        print(  # noqa: T201
            f"z4j-brain reset-setup: no DB found at {db_path}. "
            "Nothing to reset - run `z4j-brain serve` to bootstrap.",
            file=sys.stderr,
        )
        return 0

    _bootstrap_env_for_management_commands()

    from sqlalchemy import delete, select

    from z4j_brain.persistence.database import (
        DatabaseManager,
        create_engine_from_settings,
    )
    from z4j_brain.persistence.models import (
        AuditLog,
        FirstBootToken,
        User,
    )
    from z4j_brain.settings import Settings

    settings = Settings()  # type: ignore[call-arg]
    engine = create_engine_from_settings(settings)
    db = DatabaseManager(engine)

    async def _run() -> int:
        try:
            async with db.session() as session:
                first_admin = (
                    await session.execute(select(User).limit(1))
                ).scalars().first()
                if first_admin is not None:
                    print(  # noqa: T201
                        "z4j-brain reset-setup: REFUSED - an admin user "
                        "already exists. Reset-setup is only for the "
                        "pre-first-boot state. Use the dashboard's "
                        "account-recovery flow or restore from backup "
                        "if you need to regain access.",
                        file=sys.stderr,
                    )
                    return 2

                if not args.force:
                    print(  # noqa: T201
                        "About to wipe:\n"
                        "  - all pending first-boot tokens\n"
                        "  - audit-log rows where action like 'setup.%'\n"
                        "Pass --force to proceed without this prompt. "
                        "Cancelled (no --force).",
                        file=sys.stderr,
                    )
                    return 1

                tokens_deleted = (
                    await session.execute(delete(FirstBootToken))
                ).rowcount
                audit_deleted = (
                    await session.execute(
                        delete(AuditLog).where(
                            AuditLog.action.like("setup.%"),
                        ),
                    )
                ).rowcount
                await session.commit()

                print(  # noqa: T201
                    f"z4j-brain reset-setup: wiped {tokens_deleted} "
                    f"pending token(s) and {audit_deleted} audit-log "
                    "row(s). Run `z4j-brain serve` to mint a fresh "
                    "setup URL.",
                )
                return 0
        finally:
            await db.dispose()

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Full-DB reset, user mgmt, health checks, status
# ---------------------------------------------------------------------------

_TABLES_TO_WIPE_ORDER: tuple[str, ...] = (
    # Child rows first (FK constraints). Schema stays intact; only
    # the rows vanish. If you add a new table in a migration, append
    # it here so `reset` stays complete.
    "audit_log",
    "sessions",
    "first_boot_tokens",
    "password_reset_tokens",
    "api_keys",
    "commands",
    "events",
    "task_annotations",
    "tasks",
    "schedules",
    "queues",
    "workers",
    "agents",
    "notification_deliveries",
    "user_notifications",
    "user_subscriptions",
    "user_channels",
    "user_preferences",
    "notification_channels",
    "alert_events",
    "project_default_subscriptions",
    "project_config",
    "memberships",
    "invitations",
    "projects",
    "export_jobs",
    "extension_store",
    "feature_flags",
    "saved_views",
    "users",
    "z4j_meta",
)


def _bootstrap_env_for_management_commands() -> None:
    """Set Z4J_* env vars so Settings() and alembic's env.py can
    construct. Mirrors the early part of ``_run_serve`` but stops
    before instantiating anything - callers that need Settings +
    engine use :func:`_build_settings_from_env` which wraps this.

    Idempotent: safe to call multiple times. Only mints secrets
    when ``~/.z4j/secret.env`` doesn't exist.
    """
    import os
    from pathlib import Path

    if not os.environ.get("Z4J_DATABASE_URL"):
        data_dir = Path.home() / ".z4j"
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "z4j.db"
        os.environ["Z4J_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
        os.environ.setdefault("Z4J_REGISTRY_BACKEND", "local")

    if not os.environ.get("Z4J_SECRET"):
        secret_env = Path.home() / ".z4j" / "secret.env"
        if secret_env.exists():
            for line in secret_env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
        else:
            # Mint fresh secrets so management commands work on a
            # never-served install. `serve` will re-use these on
            # its first boot.
            import secrets as _secrets

            secret_env.parent.mkdir(parents=True, exist_ok=True)
            new_secret = _secrets.token_urlsafe(48)
            new_session = _secrets.token_urlsafe(48)
            secret_env.write_text(
                f"Z4J_SECRET={new_secret}\nZ4J_SESSION_SECRET={new_session}\n",
                encoding="utf-8",
            )
            try:
                secret_env.chmod(0o600)
            except OSError:
                pass
            os.environ["Z4J_SECRET"] = new_secret
            os.environ["Z4J_SESSION_SECRET"] = new_session

    os.environ.setdefault("Z4J_ENVIRONMENT", "dev")
    os.environ.setdefault(
        "Z4J_ALLOWED_HOSTS", '["localhost","127.0.0.1"]',
    )


def _build_settings_from_env() -> tuple["Any", "Any"]:
    """Shared bootstrap for commands that need a DB engine.

    Calls :func:`_bootstrap_env_for_management_commands` then
    constructs ``Settings`` + an ``AsyncEngine``.
    """
    _bootstrap_env_for_management_commands()

    from z4j_brain.persistence.database import create_engine_from_settings
    from z4j_brain.settings import Settings

    settings = Settings()  # type: ignore[call-arg]
    engine = create_engine_from_settings(settings)
    return settings, engine


def _run_reset(args: argparse.Namespace) -> int:
    """Wipe every runtime table + optionally the persisted secrets.

    Destructive: this command irrecoverably deletes all user data in
    the brain's database (users, projects, agents, tasks, events,
    schedules, audit log, notifications, etc.). Schema is preserved;
    alembic does not re-run.

    After this, the brain is in pre-first-boot state: the next
    ``serve`` mints a fresh setup token and prints a new admin-
    creation URL, just like a brand-new install.

    Use when:
      - starting over on a dev / evaluation machine
      - recovering from a test that left junk data
      - cleaning a staging environment between runs

    Do NOT use on production without a DB backup. There is no undo.
    """
    import asyncio
    import sys
    from pathlib import Path

    import structlog

    from sqlalchemy import text

    if not args.force:
        print(  # noqa: T201
            "z4j-brain reset: REQUIRED --force flag missing.\n"
            "\n"
            "This command wipes every runtime row in the brain's DB\n"
            "(users, projects, agents, tasks, events, audit log,\n"
            "sessions, ...). Irrecoverable without a backup.\n"
            "\n"
            "If you really mean it:\n"
            "  z4j-brain reset --force\n"
            "  z4j-brain reset --force --nuke-secrets   # also resets HMAC keys",
            file=sys.stderr,
        )
        return 1

    settings, engine = _build_settings_from_env()

    async def _wipe() -> int:
        from z4j_brain.persistence.database import DatabaseManager

        db = DatabaseManager(engine)
        try:
            async with db.session() as session:
                wiped_total = 0
                for table in _TABLES_TO_WIPE_ORDER:
                    try:
                        result = await session.execute(
                            text(f"DELETE FROM {table}"),
                        )
                        wiped_total += result.rowcount or 0
                    except Exception as exc:  # noqa: BLE001
                        # Table might not exist in older schemas.
                        # Log and continue - other tables still need
                        # to be wiped.
                        print(  # noqa: T201
                            f"  warning: skipping {table}: "
                            f"{type(exc).__name__}: {exc}",
                            file=sys.stderr,
                        )
                await session.commit()
                print(  # noqa: T201
                    f"z4j-brain reset: wiped {wiped_total:,} rows "
                    f"across {len(_TABLES_TO_WIPE_ORDER)} tables.",
                )
        finally:
            await db.dispose()

        if args.nuke_secrets:
            secret_env = Path.home() / ".z4j" / "secret.env"
            if secret_env.exists():
                secret_env.unlink()
                print(  # noqa: T201
                    f"z4j-brain reset: deleted {secret_env} "
                    "(next serve will mint fresh HMAC keys)",
                )

        print(  # noqa: T201
            "z4j-brain reset: done. Run `z4j-brain serve` to see "
            "the new first-boot setup URL.",
        )
        return 0

    # Silence structlog's boot-time warnings during reset.
    structlog.reset_defaults()
    return asyncio.run(_wipe())


def _run_changepassword(args: argparse.Namespace) -> int:
    """Reset a user's password from the CLI.

    Invalidates every existing session for the user (by bumping
    ``password_changed_at``), so sessions issued before this
    command fail the live-session check on their next request.
    """
    import asyncio
    import getpass
    import sys
    from datetime import UTC, datetime

    from sqlalchemy import select

    password = _read_password_from_args(args)
    if password is None:
        return 2

    settings, engine = _build_settings_from_env()

    async def _run() -> int:
        from z4j_brain.auth.passwords import PasswordHasher
        from z4j_brain.persistence.database import DatabaseManager
        from z4j_brain.persistence.models import User

        hasher = PasswordHasher(settings)
        try:
            hasher.validate_policy(password)
        except Exception as exc:  # noqa: BLE001
            print(  # noqa: T201
                f"z4j-brain changepassword: password rejected: {exc}",
                file=sys.stderr,
            )
            return 3

        db = DatabaseManager(engine)
        try:
            async with db.session() as session:
                user = (
                    await session.execute(
                        select(User).where(User.email == args.email.lower()),
                    )
                ).scalars().first()
                if user is None:
                    print(  # noqa: T201
                        f"z4j-brain changepassword: no user with email "
                        f"{args.email!r}",
                        file=sys.stderr,
                    )
                    return 4
                user.password_hash = hasher.hash(password)
                user.password_changed_at = datetime.now(UTC)
                user.failed_login_count = 0
                user.locked_until = None
                await session.commit()
                print(  # noqa: T201
                    f"z4j-brain changepassword: password updated for "
                    f"{user.email}. All existing sessions are now invalid.",
                )
                return 0
        finally:
            await db.dispose()

    return asyncio.run(_run())


def _read_password_from_args(args: argparse.Namespace) -> str | None:
    """Shared helper for password reading (stdin vs flag)."""
    import sys

    if getattr(args, "password_stdin", False):
        password = sys.stdin.read().strip()
        if not password:
            print(  # noqa: T201
                "error: empty password from stdin",
                file=sys.stderr,
            )
            return None
        return password
    if getattr(args, "password", None):
        print(  # noqa: T201
            "WARNING: password passed on the command line is visible "
            "in shell history and `ps`. Prefer --password-stdin.",
            file=sys.stderr,
        )
        return args.password
    print(  # noqa: T201
        "error: must provide --password or --password-stdin",
        file=sys.stderr,
    )
    return None


def _run_check(args: argparse.Namespace) -> int:
    """Validate config + DB connectivity + migrations-at-head.

    Non-destructive. Returns:
      0 = all green
      1 = config invalid
      2 = DB unreachable
      3 = schema not at alembic head (operator must run migrate)
    """
    import asyncio
    import sys

    from sqlalchemy import text

    checks: list[tuple[str, str]] = []

    try:
        settings, engine = _build_settings_from_env()
        checks.append(("config", "OK"))
    except Exception as exc:  # noqa: BLE001
        print(  # noqa: T201
            f"z4j-brain check: config INVALID: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    async def _db_check() -> int:
        from z4j_brain.persistence.database import DatabaseManager

        db = DatabaseManager(engine)
        try:
            try:
                async with db.session() as session:
                    await session.execute(text("SELECT 1"))
                checks.append(("database connectivity", "OK"))
            except Exception as exc:  # noqa: BLE001
                print(  # noqa: T201
                    f"z4j-brain check: DB unreachable: "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                return 2

            try:
                async with db.session() as session:
                    row = (
                        await session.execute(
                            text(
                                "SELECT version_num FROM alembic_version",
                            ),
                        )
                    ).first()
                    if row is None:
                        checks.append(("alembic version", "NOT INITIALIZED"))
                        print(  # noqa: T201
                            "z4j-brain check: alembic_version table empty "
                            "(run `z4j-brain migrate upgrade head`)",
                            file=sys.stderr,
                        )
                        return 3
                    checks.append(
                        ("alembic version", f"at {row[0]}"),
                    )
            except Exception as exc:  # noqa: BLE001
                # alembic_version table missing = fresh DB, not an error.
                checks.append(
                    ("alembic version", f"not present ({exc})"),
                )
        finally:
            await db.dispose()
        return 0

    rc = asyncio.run(_db_check())
    for name, status in checks:
        print(f"  {name:30s}  {status}")  # noqa: T201
    if rc == 0:
        print("z4j-brain check: all green.")  # noqa: T201
    return rc


def _run_status(args: argparse.Namespace) -> int:
    """Print a high-level summary of brain state.

    Intended for quick "what's going on" visibility - not a full
    health check (see `check` for that). Counts rows across the
    user-visible tables and shows the alembic HEAD revision.
    """
    import asyncio

    from sqlalchemy import func, select, text

    settings, engine = _build_settings_from_env()

    async def _run() -> int:
        from z4j_brain.persistence.database import DatabaseManager
        from z4j_brain.persistence.models import (
            Agent,
            AuditLog,
            Project,
            Session as SessionModel,
            Task,
            User,
        )

        db = DatabaseManager(engine)
        try:
            async with db.session() as session:
                async def _count(model: type) -> "int | str":
                    """Return row count, or 'n/a' if the table doesn't
                    exist yet (fresh DB, never migrated). Each call uses
                    a SAVEPOINT so a missing table on one model doesn't
                    poison the session for the others.
                    """
                    try:
                        async with session.begin_nested():
                            row = (
                                await session.execute(
                                    select(func.count()).select_from(model),
                                )
                            ).scalar_one()
                            return int(row or 0)
                    except Exception:  # noqa: BLE001
                        return "n/a"

                users = await _count(User)
                projects = await _count(Project)
                agents = await _count(Agent)
                tasks = await _count(Task)
                sessions = await _count(SessionModel)
                audit_rows = await _count(AuditLog)

                try:
                    rev_row = (
                        await session.execute(
                            text(
                                "SELECT version_num FROM alembic_version",
                            ),
                        )
                    ).first()
                    rev = rev_row[0] if rev_row else "(none)"
                except Exception:  # noqa: BLE001
                    rev = "(alembic_version missing)"

            def _fmt(v: "int | str") -> str:
                return f"{v:>8,}" if isinstance(v, int) else f"{v:>8}"

            print("z4j status")  # noqa: T201
            print(f"  version             {__version__}")  # noqa: T201
            print(f"  alembic head        {rev}")  # noqa: T201
            print(f"  environment         {settings.environment}")  # noqa: T201
            print(f"  database            {settings.database_url.split('@')[-1]}")  # noqa: T201
            print("")  # noqa: T201
            print("  row counts:")  # noqa: T201
            print(f"    users             {_fmt(users)}")  # noqa: T201
            print(f"    projects          {_fmt(projects)}")  # noqa: T201
            print(f"    agents            {_fmt(agents)}")  # noqa: T201
            print(f"    tasks             {_fmt(tasks)}")  # noqa: T201
            print(f"    active sessions   {_fmt(sessions)}")  # noqa: T201
            print(f"    audit rows        {_fmt(audit_rows)}")  # noqa: T201
            if any(v == "n/a" for v in (users, projects, agents, tasks, sessions, audit_rows)):
                print("")  # noqa: T201
                print(  # noqa: T201
                    "  (n/a = table not present yet; run "
                    "`z4j migrate upgrade head`)",
                )
            return 0
        finally:
            await db.dispose()

    return asyncio.run(_run())


def _run_bootstrap_admin(args: argparse.Namespace) -> int:
    """Imperatively create the first admin user + default project.

    Fails with a clear message (exit 3) if the brain is already
    past first-boot. This command is for the narrow case where
    someone needs to provision an admin without running uvicorn -
    e.g. a Kubernetes ``Job`` running before the brain Deployment,
    or a CI step setting up a test environment.

    Password handling: ``--password-stdin`` is the recommended
    path (nothing visible in ``ps`` or shell history). The
    ``--password`` flag is available for non-interactive scripts
    that already handle secrets elsewhere but comes with a
    printed warning.
    """
    import asyncio
    import getpass
    import os

    from z4j_brain.auth.passwords import PasswordHasher
    from z4j_brain.domain.audit_service import AuditService
    from z4j_brain.domain.setup_service import SetupService
    from z4j_brain.persistence.database import (
        DatabaseManager,
        create_engine_from_settings,
    )
    from z4j_brain.settings import Settings
    from z4j_brain.startup import run_first_boot_check

    # Password source. Either stdin (preferred) or --password.
    if args.password_stdin:
        if sys.stdin.isatty():
            password = getpass.getpass("z4j admin password: ")
        else:
            password = sys.stdin.read().rstrip("\n")
    else:
        print(  # noqa: T201
            "warning: --password is visible in ps/shell-history; "
            "use --password-stdin in production scripts",
            file=sys.stderr,
        )
        password = args.password

    if not password:
        print("error: empty password", file=sys.stderr)  # noqa: T201
        return 2

    # Bootstrap env (DB URL + secrets) so Settings() + alembic can
    # construct. Fresh-install supported.
    _bootstrap_env_for_management_commands()

    # Auto-migrate so tables exist on a truly fresh install.
    # Idempotent: no-op if already at head.
    try:
        _auto_migrate()
    except SystemExit:
        print(  # noqa: T201
            "z4j-brain bootstrap-admin: migrations failed. "
            "Run `z4j-brain migrate upgrade head` manually first.",
            file=sys.stderr,
        )
        return 2

    # Thread the env-var path inside run_first_boot_check so the
    # CLI and the env-var mode produce byte-identical outcomes.
    os.environ["Z4J_BOOTSTRAP_ADMIN_EMAIL"] = args.email
    os.environ["Z4J_BOOTSTRAP_ADMIN_PASSWORD"] = password
    if args.display_name:
        os.environ["Z4J_BOOTSTRAP_ADMIN_DISPLAY_NAME"] = args.display_name

    async def _bootstrap() -> int:
        settings = Settings()  # type: ignore[call-arg]
        db = DatabaseManager(create_engine_from_settings(settings))
        hasher = PasswordHasher(settings)
        audit = AuditService(settings)
        setup_service = SetupService(
            settings=settings, hasher=hasher, audit=audit,
        )

        # Detect "already set up" so we can return a distinct exit
        # code (3) instead of the generic 1. The shared
        # ``run_first_boot_check`` is idempotent and will simply
        # log + return in that case.
        from z4j_brain.persistence.repositories import UserRepository

        async with db.session() as session:
            users = UserRepository(session)
            if not await setup_service.is_first_boot(users):
                print(  # noqa: T201
                    "error: brain is already initialised; "
                    "use the admin UI to manage users",
                    file=sys.stderr,
                )
                return 3

        await run_first_boot_check(
            db=db, setup_service=setup_service, settings=settings,
        )
        print(f"z4j-brain: admin {args.email} provisioned")  # noqa: T201
        return 0

    return asyncio.run(_bootstrap())


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


__all__ = ["main"]
