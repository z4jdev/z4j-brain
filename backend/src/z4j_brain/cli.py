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
    """Entry point installed as the ``z4j-brain`` console script."""
    parser = argparse.ArgumentParser(
        prog="z4j-brain",
        description="z4j brain server (AGPL v3)",
    )
    sub = parser.add_subparsers(dest="command", required=False)

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
        default=10_000,
        help="maximum number of rows to verify (default: 10000)",
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

    # version
    sub.add_parser("version", help="print version")

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

    # Default to SQLite if no DATABASE_URL is set (mirror of serve).
    if not os.environ.get("Z4J_DATABASE_URL"):
        data_dir = Path.home() / ".z4j"
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "z4j.db"
        os.environ["Z4J_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
        os.environ.setdefault("Z4J_REGISTRY_BACKEND", "local")

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
