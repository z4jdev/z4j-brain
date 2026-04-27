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
            f"  {prog} --version                 # print installed version\n"
            "\n"
            f"Run `{prog} <command> --help` for per-command flags."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Standard Python convention: ``--version`` / ``-V`` print the
    # version and exit. Mirrors the existing ``z4j version``
    # subcommand and bare ``z4j`` (no subcommand) - all three paths
    # produce the same output. -v (lowercase) is intentionally NOT
    # bound here so it stays free for a future --verbose flag,
    # matching pip / docker / kubectl convention.
    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version=__version__,
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
    # --environment / --env: CLI shortcut for setting Z4J_ENVIRONMENT.
    # Wins over the env var (CLI > env > auto-detect default). Most
    # operators set this once via systemd Environment= and never
    # touch it; the flag exists for one-off testing ("does this work
    # in production mode without restart loops?") and for the dev
    # workflow ("flip to production for a smoke test, back to dev
    # for the next iteration"). The choices are deliberately strict
    # - no `prod` shorthand because Settings.environment is also
    # a string field and accepting `prod` here would create a path
    # that bypasses Settings's own validation.
    serve.add_argument(
        "--environment",
        "--env",
        default=None,
        choices=("dev", "production"),
        metavar="MODE",
        help=(
            "set the brain's security posture (dev | production). "
            "Wins over Z4J_ENVIRONMENT. dev = loopback-only bind, "
            "relaxed cookies, no HSTS, host validation off. production = "
            "TLS-required (Z4J_PUBLIC_URL must be https://), explicit "
            "Z4J_ALLOWED_HOSTS required, Secure cookies + __Host- prefix, "
            "HSTS sent. Default: production if both Z4J_PUBLIC_URL=https:// "
            "and Z4J_ALLOWED_HOSTS are set, else dev."
        ),
    )
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
    serve.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        metavar="HOST",
        help=(
            "Add a host to the Host: header allow-list. Repeatable. "
            "Merged with Z4J_ALLOWED_HOSTS env, the auto-detected system "
            "hostname, and localhost. Use this when you reach the brain "
            "via a hostname or IP that the auto-detect missed - e.g. "
            "`--allowed-host brain.internal.lan`."
        ),
    )
    serve.add_argument(
        "--debug-host-errors",
        action="store_true",
        help=(
            "DEV ONLY: include the rejected Host header, the configured "
            "allow-list, and a fix command in the body of the 400 response. "
            "Default behaviour returns a minimal `{error,message,request_id}` "
            "body so reverse-proxy / public-internet callers cannot enumerate "
            "internal hostnames. Sets Z4J_DEBUG_HOST_ERRORS=1 for the "
            "middleware. Refused entirely outside dev mode."
        ),
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

    # allowed-hosts (manage the persistent ~/.z4j/allowed-hosts file)
    ah = sub.add_parser(
        "allowed-hosts",
        help=(
            "manage the persistent Host: header allow-list at "
            "~/.z4j/allowed-hosts. Hosts added here are merged into the "
            "auto-detect set on every `z4j serve` start, so you don't "
            "need to set Z4J_ALLOWED_HOSTS or pass --allowed-host every "
            "time."
        ),
    )
    ah_sub = ah.add_subparsers(
        dest="ah_action",
        required=True,
        title="actions",
        metavar="<action>",
    )
    ah_sub.add_parser("list", help="print the current persisted allow-list")
    ah_add = ah_sub.add_parser("add", help="add one or more hosts to the file")
    ah_add.add_argument("hosts", nargs="+", metavar="HOST",
                        help="hostname or IP literal to allow")
    ah_rm = ah_sub.add_parser("remove", help="remove one or more hosts from the file")
    ah_rm.add_argument("hosts", nargs="+", metavar="HOST",
                       help="hostname or IP literal to remove")
    ah_sub.add_parser("path", help="print the file path the brain reads from")

    # doctor
    sub.add_parser(
        "doctor",
        help=(
            "run a full health + configuration audit: check (config/DB/"
            "migrations) + status (counts) + warnings for common pitfalls "
            "(dev mode + public bind, no admin user, secrets file "
            "un-backed-up, etc.). Use this before exposing the brain to "
            "the internet or before a release."
        ),
    )

    # backup
    backup = sub.add_parser(
        "backup",
        help=(
            "snapshot the brain database to a single file. SQLite uses "
            "VACUUM INTO (online; brain keeps serving). PostgreSQL "
            "shells out to pg_dump (custom format)."
        ),
    )
    backup.add_argument(
        "--output",
        "-o",
        required=True,
        metavar="PATH",
        help="output file path (e.g. ./z4j-2026-04-24.dump)",
    )

    # restore
    restore = sub.add_parser(
        "restore",
        help=(
            "restore the brain database from a backup file. STOP the "
            "brain process before running. SQLite replaces the live "
            "DB file (existing one preserved as .pre-restore-bak). "
            "PostgreSQL uses pg_restore --clean --if-exists."
        ),
    )
    restore.add_argument(
        "source",
        metavar="PATH",
        help="path to a backup file produced by `z4j backup`",
    )
    restore.add_argument(
        "--force",
        action="store_true",
        help="acknowledge that the brain process is stopped",
    )

    # metrics-token
    mt = sub.add_parser(
        "metrics-token",
        help=(
            "manage the /metrics bearer token (auto-minted on first "
            "boot, persisted to ~/.z4j/secret.env). Default action "
            "prints the token; `rotate` mints a new one."
        ),
    )
    mt_sub = mt.add_subparsers(
        dest="metrics_action",
        title="actions",
        metavar="<action>",
    )
    mt_sub.add_parser(
        "show",
        help="print the current token (default action when no <action>)",
    )
    mt_sub.add_parser(
        "rotate",
        help=(
            "mint a fresh token, replace it in ~/.z4j/secret.env, and "
            "print the new value. Requires a brain restart for the new "
            "token to take effect on the live process - the running "
            "brain still validates against the old token in memory until "
            "it re-reads secret.env at startup. Update your Prometheus "
            "scrape config with the new token before restarting."
        ),
    )

    # mint-scheduler-cert
    msc = sub.add_parser(
        "mint-scheduler-cert",
        help=(
            "mint a fresh mTLS client certificate for a z4j-scheduler "
            "instance. Requires the brain operator's CA cert + key "
            "(typically the same CA that signed the brain's gRPC "
            "server cert). Writes <name>.crt and <name>.key into "
            "--out-dir with mode 0600."
        ),
    )
    msc.add_argument(
        "--name",
        required=True,
        help=(
            "CN + DNS SAN of the cert (e.g. 'scheduler-1'). Add this "
            "value to Z4J_SCHEDULER_GRPC_ALLOWED_CNS on the brain "
            "before deploying the cert."
        ),
    )
    msc.add_argument(
        "--ca-cert",
        required=True,
        metavar="PATH",
        help="path to the CA certificate (PEM)",
    )
    msc.add_argument(
        "--ca-key",
        required=True,
        metavar="PATH",
        help="path to the CA private key (PEM, unencrypted)",
    )
    msc.add_argument(
        "--out-dir",
        required=True,
        metavar="PATH",
        help="directory where <name>.crt and <name>.key will be written",
    )
    msc.add_argument(
        "--validity-days",
        type=int,
        default=365,
        help="certificate validity in days (default: 365)",
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

    if args.command == "allowed-hosts":
        return _run_allowed_hosts(args)

    if args.command == "backup":
        return _run_backup(args)

    if args.command == "restore":
        return _run_restore(args)

    if args.command == "metrics-token":
        return _run_metrics_token(args)

    if args.command == "doctor":
        return _run_doctor(args)

    if args.command == "mint-scheduler-cert":
        return _run_mint_scheduler_cert(args)

    parser.error(f"unknown command {args.command!r}")
    return 2


def _run_mint_scheduler_cert(args: argparse.Namespace) -> int:
    """Dispatch ``z4j mint-scheduler-cert``.

    Reads the operator's CA material, mints a fresh cert + key for
    the named scheduler instance, writes them to ``--out-dir``, and
    prints the on-disk paths so the operator can scp them to the
    scheduler host.

    Imported lazily so the ``cryptography`` dep is only required for
    operators who actually run this command.
    """
    try:
        from z4j_brain.scheduler_grpc.auth import (  # noqa: PLC0415
            mint_scheduler_cert,
            write_minted_cert,
        )
    except ImportError as exc:
        print(
            "z4j: mint-scheduler-cert requires the scheduler-grpc extra. "
            "Install with: pip install 'z4j[scheduler-grpc]'",
            file=sys.stderr,
        )
        print(f"  underlying error: {exc}", file=sys.stderr)
        return 2

    ca_cert_path = Path(args.ca_cert)
    ca_key_path = Path(args.ca_key)
    out_dir = Path(args.out_dir)

    if not ca_cert_path.is_file():
        print(f"z4j: --ca-cert {ca_cert_path!s} not found", file=sys.stderr)
        return 2
    if not ca_key_path.is_file():
        print(f"z4j: --ca-key {ca_key_path!s} not found", file=sys.stderr)
        return 2

    try:
        cert_pem, key_pem = mint_scheduler_cert(
            name=args.name,
            ca_cert_pem=ca_cert_path.read_bytes(),
            ca_key_pem=ca_key_path.read_bytes(),
            validity_days=args.validity_days,
        )
        cert_path, key_path = write_minted_cert(
            out_dir=out_dir,
            name=args.name,
            cert_pem=cert_pem,
            key_pem=key_pem,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"z4j mint-scheduler-cert failed: {exc}", file=sys.stderr)
        return 1

    print(f"wrote certificate: {cert_path}")
    print(f"wrote private key: {key_path}")
    print(
        f"\nNext steps:\n"
        f"  1. Add '{args.name}' to Z4J_SCHEDULER_GRPC_ALLOWED_CNS on the brain\n"
        f"  2. Restart the brain so the new allow-list takes effect\n"
        f"  3. scp {cert_path.name} {key_path.name} to the scheduler host\n"
        f"  4. Set Z4J_SCHEDULER_TLS_CERT and Z4J_SCHEDULER_TLS_KEY on the scheduler",
    )
    return 0


def _run_metrics_token(args: argparse.Namespace) -> int:
    """Dispatch ``z4j metrics-token [show|rotate]``.

    Default (no action) is ``show`` for backward compatibility with
    1.0.13's ``z4j metrics-token`` (no subcommand).
    """
    action = getattr(args, "metrics_action", None) or "show"
    if action == "rotate":
        return _run_metrics_token_rotate(args)
    return _run_metrics_token_show(args)


def _run_metrics_token_show(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Print the ``/metrics`` bearer token.

    Resolution (first match wins):
      1. ``Z4J_METRICS_AUTH_TOKEN`` env var (operator override).
      2. ``Z4J_METRICS_AUTH_TOKEN`` line in ``~/.z4j/secret.env``
         (auto-minted by ``z4j serve`` on first boot).
      3. Prints an error to stderr and exits 2.

    Writes ONLY the token to stdout so scripts can use
    ``$(z4j metrics-token)`` safely.
    """
    import os
    from pathlib import Path as _Path

    token = os.environ.get("Z4J_METRICS_AUTH_TOKEN")
    if token:
        print(token)  # noqa: T201
        return 0

    secret_env = _Path.home() / ".z4j" / "secret.env"
    if secret_env.exists():
        for line in secret_env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("Z4J_METRICS_AUTH_TOKEN="):
                print(line.split("=", 1)[1])  # noqa: T201
                return 0

    print(  # noqa: T201
        "z4j metrics-token: no token found. "
        "Run `z4j serve` once to auto-mint one, or set "
        "Z4J_METRICS_AUTH_TOKEN explicitly.",
        file=sys.stderr,
    )
    return 2


def _run_metrics_token_rotate(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Mint a fresh ``/metrics`` bearer token and replace it in
    ``~/.z4j/secret.env``.

    Atomically rewrites the file: read all lines, replace (or
    append) the ``Z4J_METRICS_AUTH_TOKEN=`` line, write to a temp
    file in the same dir, ``rename()`` over the original. This way
    a concurrent ``z4j serve`` boot reads either the old file or
    the new one, never a half-written one.

    Does NOT touch the running brain process. Operators must
    restart for the new token to take effect (the brain caches
    the env var at startup; FastAPI's ``Settings`` is built once
    per process).

    Prints the new token to stdout (one line, no other noise) so
    scripts can ``new=$(z4j metrics-token rotate)`` and immediately
    push the value to a Prometheus reload.
    """
    import os
    import secrets as _secrets
    from pathlib import Path as _Path

    secret_env = _Path.home() / ".z4j" / "secret.env"
    if not secret_env.exists():
        print(  # noqa: T201
            "z4j metrics-token rotate: ~/.z4j/secret.env does not exist. "
            "Run `z4j serve` once to auto-mint the secret store first.",
            file=sys.stderr,
        )
        return 2

    new_token = _secrets.token_urlsafe(32)
    lines = secret_env.read_text(encoding="utf-8").splitlines()
    out_lines: list[str] = []
    found = False
    for line in lines:
        if line.strip().startswith("Z4J_METRICS_AUTH_TOKEN="):
            out_lines.append(f"Z4J_METRICS_AUTH_TOKEN={new_token}")
            found = True
        else:
            out_lines.append(line)
    if not found:
        # Pre-1.0.13 file with no metrics token line. Append.
        out_lines.append(f"Z4J_METRICS_AUTH_TOKEN={new_token}")
    new_content = ("\n".join(out_lines) + "\n").encode("utf-8")

    # Atomic write: tmp file in the same directory, then rename. The
    # tmp is created with O_EXCL + 0o600 from the start (audit M-1) so
    # no race window exists where a local user could read the new
    # token from the temp file before chmod tightens it. Pre-1.0.14
    # used Path.write_text which created the file with the process
    # umask (typically 0o644) and only narrowed it after; on Windows
    # the followup chmod is a no-op so the file inherited the parent
    # dir's ACLs.
    tmp = secret_env.with_suffix(secret_env.suffix + ".rotate-tmp")
    # If a previous rotate crashed mid-write, the EXCL would refuse
    # to overwrite a stale tmp. Best-effort cleanup first.
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY  # Windows: no implicit \r\n translation
    fd = os.open(str(tmp), flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(new_content)
    except Exception:
        # Roll back the tmp file if the write fails so we don't leave
        # a half-written sibling. Re-raise so the operator sees the
        # original error - rotation should fail loudly, not silently.
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    # On POSIX chmod is redundant (we already opened with 0o600) but
    # this defends against rare umask-application bugs. On Windows
    # chmod is a no-op; ACL handling is the file system's job.
    if hasattr(os, "chmod"):
        try:
            os.chmod(tmp, 0o600)
        except OSError as exc:
            print(  # noqa: T201
                f"z4j metrics-token rotate: WARNING - chmod 0o600 on "
                f"{tmp} failed: {exc}. The new token may be readable "
                f"to other local users until you tighten the file "
                f"permissions manually.",
                file=sys.stderr,
            )
    os.replace(tmp, secret_env)

    # Audit log (best-effort): log the rotation to structlog so the
    # operations team can correlate "Prometheus stopped scraping" with
    # "someone rotated the token at 14:32". We deliberately don't
    # write to the DB audit_events table from the CLI rotate path -
    # the brain may not be running, and adding a DB dependency to a
    # CLI hygiene command would be a footgun (rotate would fail when
    # the DB was unreachable).
    import logging as _logging

    _logging.getLogger("z4j.brain.cli").info(
        "metrics_token_rotated",
        extra={
            "secret_env": str(secret_env),
            "uid": os.getuid() if hasattr(os, "getuid") else None,
        },
    )

    print(new_token)  # noqa: T201
    print(  # noqa: T201
        f"z4j metrics-token rotate: new token written to {secret_env}. "
        f"Restart the brain (`systemctl restart z4j` or equivalent) for "
        f"the new token to take effect, and update your Prometheus "
        f"scrape config's authorization.credentials before the restart.",
        file=sys.stderr,
    )
    return 0


def _run_doctor(args: argparse.Namespace) -> int:
    """Full health + configuration audit.

    Composes ``check`` (DB + migrations) with a set of warnings that
    a plain ``check`` can't raise because they're not failures -
    they're configuration smells the operator should be aware of
    before exposing the brain to the internet.

    Return codes:
      0 = all green, no warnings
      0 = check passed but one or more warnings (operator attention)
      non-zero = same as ``check`` (config invalid / DB unreachable /
                 schema not at head)
    """
    import asyncio
    import os
    from pathlib import Path as _Path

    # Reuse the existing check to catch config / DB / migration issues
    # up front. If that fails, the rest of doctor is moot.
    rc = _run_check(args)
    if rc != 0:
        return rc

    warnings: list[str] = []

    # Warning 1: dev mode + non-loopback bind = publicly-reachable dev
    # mode, which is the exact footgun we already fixed in the host
    # middleware. Surface it up front so operators see it.
    env = os.environ.get("Z4J_ENVIRONMENT", "").lower()
    # Only flag when Z4J_BIND_HOST is explicitly set to a non-loopback
    # value AND env is dev. Pre-1.0.14 the default fallback was
    # "0.0.0.0", which fired a false positive every time the operator
    # ran `z4j doctor` between sessions (the env var is unset until
    # `z4j serve` sets it during dev-mode auto-defaulting). v1.0.14's
    # _run_serve sets Z4J_BIND_HOST=127.0.0.1 when env=dev and the
    # operator hasn't pinned it - so the only path to this warning is
    # an EXPLICIT Z4J_BIND_HOST != loopback set in the operator's
    # environment, which IS the dangerous combo.
    bind_host = os.environ.get("Z4J_BIND_HOST", "")
    if env == "dev" and bind_host and bind_host not in (
        "127.0.0.1", "localhost", "[::1]",
    ):
        # As of v1.0.14 `z4j serve` refuses to start with this combo
        # (see _run_serve fail-closed gate). Doctor still flags it as
        # an INFO-level warning so an operator running `z4j doctor`
        # in a CI/IaC pipeline catches the env-var combo before it
        # crashes the systemd unit on the next restart.
        warnings.append(
            f"Z4J_ENVIRONMENT=dev AND Z4J_BIND_HOST={bind_host!r}. "
            f"`z4j serve` will REFUSE to start with this combo "
            f"(fail-closed since v1.0.14). Either set "
            f"Z4J_BIND_HOST=127.0.0.1 for localhost-only dev, or switch "
            f"to Z4J_ENVIRONMENT=production with explicit "
            f"Z4J_PUBLIC_URL=https://... and Z4J_ALLOWED_HOSTS for "
            f"public access. Setting both auto-promotes the environment "
            f"to production. The CLI flag `z4j serve --environment "
            f"production` is the easiest way to flip."
        )

    # Warning 2: Z4J_DEBUG_HOST_ERRORS is on - verbose host rejection
    # responses will leak internal hostnames. Only safe for strictly-
    # localhost installs.
    if os.environ.get("Z4J_DEBUG_HOST_ERRORS", "").lower() in ("1", "true", "yes", "on"):
        warnings.append(
            "Z4J_DEBUG_HOST_ERRORS=1 is set. Rejected Host-header "
            "requests will echo internal allow-list data back to the "
            "caller. Only safe for local-laptop development bound to "
            "127.0.0.1. Turn it off for anything reachable from the "
            "network."
        )

    # Warning 3: auto-minted secrets - the operator should back up
    # the persisted secret.env file off-host.
    secret_env = _Path.home() / ".z4j" / "secret.env"
    if secret_env.exists():
        warnings.append(
            f"Brain secrets were auto-minted and persisted to "
            f"{secret_env}. This file is the ONLY copy of Z4J_SECRET and "
            f"Z4J_SESSION_SECRET for this install. Losing it invalidates "
            f"every existing agent HMAC and the audit chain. Back it up "
            f"off-host now (rsync / S3 / password manager)."
        )

    # Warning 4: /metrics exposed without auth. Prometheus labels
    # expose project IDs, queue names, task names, in-memory state.
    # Fail-secure default was introduced in 1.0.13; before that, every
    # install was public by default.
    if os.environ.get("Z4J_METRICS_PUBLIC", "").lower() in ("1", "true", "yes", "on"):
        warnings.append(
            "Z4J_METRICS_PUBLIC=1 is set. /metrics is served without "
            "authentication. Prometheus labels leak project IDs, "
            "queue/task names, and in-memory state to anyone who can "
            "reach the endpoint. Only safe on a trusted closed "
            "network. For production, unset Z4J_METRICS_PUBLIC and "
            "use Z4J_METRICS_AUTH_TOKEN (run `z4j metrics-token` to "
            "print the auto-minted value)."
        )

    async def _row_warnings() -> None:
        from sqlalchemy import text
        from z4j_brain.persistence.database import DatabaseManager

        _settings, engine = _build_settings_from_env()
        db = DatabaseManager(engine)
        try:
            async with db.session() as session:
                r_users = await session.execute(text("SELECT COUNT(*) FROM users"))
                users = r_users.scalar_one() or 0
                r_projects = await session.execute(
                    text("SELECT COUNT(*) FROM projects"),
                )
                projects = r_projects.scalar_one() or 0
                r_agents = await session.execute(text("SELECT COUNT(*) FROM agents"))
                agents = r_agents.scalar_one() or 0
        finally:
            await engine.dispose()

        if users == 0:
            warnings.append(
                "No users exist yet. Complete first-boot setup at the "
                "/setup URL printed by `z4j serve`, or run "
                "`z4j createsuperuser` directly."
            )
        if users > 0 and projects == 0:
            warnings.append(
                "Users exist but no projects. The first-boot flow normally "
                "creates a default project - investigate (`z4j status`)."
            )
        if projects > 0 and agents == 0:
            warnings.append(
                "Projects exist but no agents minted. Go to "
                "/projects/<slug>/agents in the dashboard and click "
                "'new agent' to issue a token + hmac_secret."
            )

    try:
        asyncio.run(_row_warnings())
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"could not enumerate users/projects/agents: "
            f"{type(exc).__name__}: {exc}",
        )

    if warnings:
        print("\nz4j doctor: warnings ({}):".format(len(warnings)))  # noqa: T201
        for i, w in enumerate(warnings, 1):
            print(f"  {i}. {w}\n")  # noqa: T201
        return 0

    print("\nz4j doctor: all green, no warnings.")  # noqa: T201
    return 0


def _run_backup(args: argparse.Namespace) -> int:
    """Snapshot the brain DB to a file. Backend auto-detected from DB URL."""
    _bootstrap_env_for_management_commands()
    from z4j_brain.backup import backup
    from z4j_brain.settings import Settings

    settings = Settings()  # type: ignore[call-arg]
    output = Path(args.output)
    try:
        result = backup(settings.database_url, output)
    except FileExistsError as exc:
        print(f"z4j-brain: {exc}")  # noqa: T201
        return 1
    except FileNotFoundError as exc:
        print(f"z4j-brain: {exc}")  # noqa: T201
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"z4j-brain: backup failed: {exc}")  # noqa: T201
        return 1
    size_mb = result["size_bytes"] / (1024 * 1024)
    print(  # noqa: T201
        f"z4j-brain: backup complete\n"
        f"  backend:    {result['backend']}\n"
        f"  output:     {result['path']}\n"
        f"  size:       {size_mb:.2f} MiB",
    )
    print(  # noqa: T201
        f"z4j-brain: move this file off-host (scp, rclone, S3, ...) for "
        f"true disaster recovery.",
    )
    return 0


def _run_restore(args: argparse.Namespace) -> int:
    """Restore the brain DB from a backup file. Brain MUST be stopped."""
    if not args.force:
        print(  # noqa: T201
            "z4j-brain: restore replaces the live DB. The brain process "
            "MUST be stopped first (`systemctl stop z4j` / `docker compose "
            "down z4j-brain`). Re-run with --force to acknowledge.",
        )
        return 1
    _bootstrap_env_for_management_commands()
    from z4j_brain.backup import restore
    from z4j_brain.settings import Settings

    settings = Settings()  # type: ignore[call-arg]
    src = Path(args.source)
    try:
        result = restore(settings.database_url, src)
    except FileNotFoundError as exc:
        print(f"z4j-brain: {exc}")  # noqa: T201
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"z4j-brain: restore failed: {exc}")  # noqa: T201
        return 1
    print(  # noqa: T201
        f"z4j-brain: restore complete\n"
        f"  backend:    {result['backend']}\n"
        f"  source:     {result['source']}",
    )
    print(  # noqa: T201
        f"z4j-brain: start the brain (`systemctl start z4j` / `docker "
        f"compose up -d z4j-brain`) and verify with `z4j check && z4j status`.",
    )
    return 0


def _run_allowed_hosts(args: argparse.Namespace) -> int:
    """Handle ``z4j allowed-hosts {list,add,remove,path}``.

    Thin wrapper over :mod:`z4j_brain.allowed_hosts`. All actions
    operate on the same on-disk file so a `serve` invocation reads
    exactly what an earlier `add` wrote.
    """
    from z4j_brain.allowed_hosts import add, get_path, read_persisted, remove

    action = args.ah_action

    if action == "path":
        print(get_path())  # noqa: T201
        return 0

    if action == "list":
        path = get_path()
        hosts = read_persisted()
        if not hosts:
            print(f"(no persisted hosts in {path})")  # noqa: T201
            print(  # noqa: T201
                "Add one with: z4j allowed-hosts add tasks.example.com",
            )
            return 0
        print(f"# persisted hosts ({path}):")  # noqa: T201
        for h in hosts:
            print(f"  {h}")  # noqa: T201
        print(  # noqa: T201
            "\nThese are merged into the auto-detected hostname/IP "
            "set on every `z4j serve` start.",
        )
        return 0

    if action == "add":
        added, skipped = add(args.hosts)
        for h in added:
            print(f"  added:   {h}")  # noqa: T201
        for h in skipped:
            print(f"  skipped: {h} (already present)")  # noqa: T201
        if added:
            print(  # noqa: T201
                f"\nWrote {get_path()}. Restart `z4j serve` for the "
                f"change to take effect.",
            )
        return 0

    if action == "remove":
        removed, not_found = remove(args.hosts)
        for h in removed:
            print(f"  removed:   {h}")  # noqa: T201
        for h in not_found:
            print(f"  not found: {h}")  # noqa: T201
        if removed:
            print(  # noqa: T201
                f"\nWrote {get_path()}. Restart `z4j serve` for the "
                f"change to take effect.",
            )
        return 0

    return 2


def _run_serve(args: argparse.Namespace) -> int:
    """Run uvicorn programmatically.

    We import uvicorn lazily so ``z4j-brain version`` and
    ``z4j-brain migrate`` do not pay the uvicorn import cost.
    """
    import os

    import uvicorn

    # --environment / --env CLI flag wins over Z4J_ENVIRONMENT env var
    # (CLI > env > auto-detect default). We set it BEFORE the rest of
    # the env-var defaulting below so the auto-promote and bind-host
    # logic see the operator's intent. Removing the env var first is
    # the cleanest way to express "the flag is now authoritative" -
    # otherwise os.environ.setdefault would race against an existing
    # setting from the caller's shell.
    if args.environment:
        os.environ["Z4J_ENVIRONMENT"] = args.environment

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
            # Pre-1.0.13 secret.env files lack Z4J_METRICS_AUTH_TOKEN.
            # Mint one now and append so in-place upgrades pick up the
            # new fail-secure /metrics default without operator action.
            # Done here (not at the callsite) so the appended token
            # shows up in the same secret.env file the operator already
            # knows about; no second location to discover.
            if not os.environ.get("Z4J_METRICS_AUTH_TOKEN"):
                import secrets as _secrets  # local alias, avoid shadow

                new_metrics = _secrets.token_urlsafe(32)
                with secret_env.open("a", encoding="utf-8") as fh:
                    fh.write(f"Z4J_METRICS_AUTH_TOKEN={new_metrics}\n")
                os.environ["Z4J_METRICS_AUTH_TOKEN"] = new_metrics
                print(  # noqa: T201
                    f"z4j-brain: minted Z4J_METRICS_AUTH_TOKEN (in-place "
                    f"upgrade from pre-1.0.13), appended to {secret_env}. "
                    f"/metrics now requires Authorization: Bearer <token>. "
                    f"Run `z4j metrics-token` to print it for Prometheus "
                    f"scrape config, or set Z4J_METRICS_PUBLIC=1 to opt "
                    f"back into unauthenticated scraping (not recommended).",
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
            new_metrics = _secrets.token_urlsafe(32)
            secret_env.write_text(
                f"Z4J_SECRET={new_secret}\n"
                f"Z4J_SESSION_SECRET={new_session}\n"
                f"Z4J_METRICS_AUTH_TOKEN={new_metrics}\n",
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
            os.environ["Z4J_METRICS_AUTH_TOKEN"] = new_metrics
            print(  # noqa: T201
                f"z4j-brain: minted fresh Z4J_SECRET + Z4J_SESSION_SECRET + "
                f"Z4J_METRICS_AUTH_TOKEN, persisted to {secret_env}",
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
        # Auto-promote to production when the operator's already-declared
        # config shape *is* production-shaped. Two signals taken together
        # (added v1.0.14):
        #   1. Z4J_PUBLIC_URL starts with https:// (operator wired TLS)
        #   2. Z4J_ALLOWED_HOSTS is set explicitly (operator has named
        #      the public hostnames)
        # Either alone is ambiguous; both together prove production
        # intent. Honor it instead of silently shipping dev-mode cookies
        # behind their TLS terminator. Operator can still force dev with
        # an explicit Z4J_ENVIRONMENT=dev (env always wins over our
        # defaulting).
        if "Z4J_ENVIRONMENT" not in os.environ:
            pub = os.environ.get("Z4J_PUBLIC_URL", "")
            has_allowed = "Z4J_ALLOWED_HOSTS" in os.environ
            if pub.startswith("https://") and has_allowed:
                os.environ["Z4J_ENVIRONMENT"] = "production"
                print(  # noqa: T201
                    "z4j-brain: auto-promoting Z4J_ENVIRONMENT=production "
                    "(detected https Z4J_PUBLIC_URL + explicit "
                    "Z4J_ALLOWED_HOSTS). Set Z4J_ENVIRONMENT=dev to override.",
                )
            else:
                os.environ["Z4J_ENVIRONMENT"] = "dev"
        # Smart default allow-list: localhost + the machine's own hostname
        # and FQDN. Lets `pip install z4j && z4j serve` on a remote VM
        # work without the operator having to set Z4J_ALLOWED_HOSTS for
        # the hostname they already know they're reaching. Operator can
        # override by setting Z4J_ALLOWED_HOSTS explicitly (env wins),
        # or by adding --allowed-host flags (merged below).
        if "Z4J_ALLOWED_HOSTS" not in os.environ:
            import json as _json
            import socket as _socket

            auto_hosts: list[str] = ["localhost", "127.0.0.1", "[::1]"]

            # 1) Hostname + FQDN. The hostname is what `uname -n` shows;
            #    the FQDN includes the domain (e.g. Tailscale's
            #    `<host>.<tailnet>.ts.net`).
            for fn_name in ("gethostname", "getfqdn"):
                try:
                    h = getattr(_socket, fn_name)()
                    if h and h.lower() not in {x.lower() for x in auto_hosts}:
                        auto_hosts.append(h)
                except Exception:  # noqa: BLE001
                    pass

            # 2) IPv4 addresses bound on the host. Covers the common
            #    homelab/LAN case where the operator reaches the brain
            #    via the server's LAN IP (e.g. 192.168.x.x). Without
            #    this users hit the host-validation 400 even though
            #    they're on the same network.
            #
            #    Two complementary strategies, both stdlib:
            #    a) gethostbyname_ex(hostname) returns every IP the
            #       resolver knows for the hostname. Picks up multiple
            #       interfaces on machines with proper /etc/hosts.
            #    b) UDP-socket trick: open a datagram socket "to" a
            #       non-routable address; no packet ever leaves, but
            #       the OS picks the source IP it WOULD use for that
            #       destination. That's the box's primary outbound
            #       interface IP, even on systems where (a) only
            #       returns 127.0.1.1 (Debian default).
            try:
                _, _, addrs = _socket.gethostbyname_ex(_socket.gethostname())
                for ip in addrs:
                    if ip and ip.lower() not in {x.lower() for x in auto_hosts}:
                        auto_hosts.append(ip)
            except Exception:  # noqa: BLE001
                pass
            try:
                with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as _s:
                    # 10.255.255.255 is non-routable; connect() on a UDP
                    # socket just picks the source - no datagram is sent.
                    _s.connect(("10.255.255.255", 1))
                    primary_ip = _s.getsockname()[0]
                    if primary_ip and primary_ip not in auto_hosts:
                        auto_hosts.append(primary_ip)
            except Exception:  # noqa: BLE001
                pass

            # 3) Merge persisted allow-list from `~/.z4j/allowed-hosts`.
            # Operators add custom domains here once via
            # `z4j allowed-hosts add tasks.example.com`; the file is read
            # on every boot. This is the answer to "where do I put my
            # public DNS name so I don't have to pass --allowed-host
            # every time".
            from z4j_brain.allowed_hosts import read_persisted

            for h in read_persisted():
                if h and h.lower() not in {x.lower() for x in auto_hosts}:
                    auto_hosts.append(h)

            os.environ["Z4J_ALLOWED_HOSTS"] = _json.dumps(auto_hosts)

        # SAFE-BY-DEFAULT BIND (added v1.0.14, breaking from 1.0.13):
        # In dev mode, force the bind host to loopback unless the
        # operator explicitly set Z4J_BIND_HOST. Pre-1.0.14 the default
        # was 0.0.0.0 in dev mode, which silently exposed dev-mode
        # cookies (secure=False, no __Host- prefix, no HSTS) to any
        # caller that could reach the port. Combined with the
        # fail-closed gate in _run_serve, the brain now refuses to
        # accept off-loopback connections without explicit production
        # mode (Z4J_ENVIRONMENT=production + https Z4J_PUBLIC_URL +
        # explicit Z4J_ALLOWED_HOSTS).
        if (
            os.environ.get("Z4J_ENVIRONMENT") == "dev"
            and "Z4J_BIND_HOST" not in os.environ
        ):
            os.environ["Z4J_BIND_HOST"] = "127.0.0.1"

        # PUBLIC_URL AUTO-DERIVATION (added v1.0.14): when the operator
        # hasn't pinned Z4J_PUBLIC_URL, derive it from the actual bind
        # host:port so the first-boot setup banner prints a URL that
        # actually works. Pre-1.0.14 the banner hard-coded
        # http://localhost:7700/setup?token=... regardless of --port,
        # leaving anyone running on a non-default port staring at a
        # 404 from whatever happened to be on :7700 (or a connection
        # refusal). Production mode requires Z4J_PUBLIC_URL to be
        # set explicitly (see Settings._enforce_security_invariants),
        # so this fires only in dev mode where the default is
        # operator-friendly rather than security-load-bearing.
        if (
            os.environ.get("Z4J_ENVIRONMENT") == "dev"
            and "Z4J_PUBLIC_URL" not in os.environ
        ):
            # Mirror the precedence uvicorn will use:
            # --host > Z4J_BIND_HOST > settings default.
            # --port > Z4J_BIND_PORT > settings default (7700).
            _bh = args.host or os.environ.get("Z4J_BIND_HOST", "127.0.0.1")
            _bp = args.port or int(os.environ.get("Z4J_BIND_PORT", "7700"))
            # Browsers prefer "localhost" over "127.0.0.1" / "::1" in
            # display (it survives clipboard better and works
            # cross-IPv4/IPv6). For non-loopback binds we keep the
            # actual host so the URL still resolves; non-loopback in
            # dev mode is fail-closed anyway, so this branch is mostly
            # belt-and-suspenders.
            _display = (
                "localhost"
                if _bh in ("127.0.0.1", "localhost", "[::1]", "::1", "0.0.0.0")
                else _bh
            )
            os.environ["Z4J_PUBLIC_URL"] = f"http://{_display}:{_bp}"

    # --debug-host-errors opt-in: enables verbose host-rejection response
    # bodies, but ONLY in dev mode. Protects against the common footgun
    # where a homelab operator runs the pip/SQLite path (dev mode by
    # default) behind a public reverse proxy - they'd otherwise get
    # internal-hostname leakage on every crawler hit.
    if getattr(args, "debug_host_errors", False):
        if os.environ.get("Z4J_ENVIRONMENT", "").lower() != "dev":
            print(  # noqa: T201
                "z4j-brain: --debug-host-errors refused outside dev mode. "
                "This flag enables verbose 400 responses that leak internal "
                "hostnames; unsafe when the brain is reachable from any "
                "source other than localhost.",
            )
            return 1
        os.environ["Z4J_DEBUG_HOST_ERRORS"] = "1"
        print(  # noqa: T201
            "z4j-brain: WARNING - --debug-host-errors is ON. Rejected "
            "requests will return internal hostnames in the response body. "
            "For local development only.",
        )

    # Merge any --allowed-host CLI flags onto whatever env / auto-detect
    # produced. The CLI flag is a repeatable convenience for ad-hoc hosts
    # ("this VM's DNS name", "an internal load balancer", ...) - it never
    # replaces the env, only extends it.
    if getattr(args, "allowed_host", None):
        import json as _json

        current = os.environ.get("Z4J_ALLOWED_HOSTS", "[]").strip()
        try:
            existing = _json.loads(current) if current else []
            if not isinstance(existing, list):
                existing = []
        except Exception:  # noqa: BLE001
            # Tolerate a comma-separated string in the env var - some
            # operators reach for the shell-native form.
            existing = [s.strip() for s in current.split(",") if s.strip()]
        merged = list(existing)
        for h in args.allowed_host:
            if h and h not in merged:
                merged.append(h)
        os.environ["Z4J_ALLOWED_HOSTS"] = _json.dumps(merged)

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

    # FAIL-CLOSED dev+public-bind gate (added v1.0.14, breaking from
    # 1.0.13). Refuse to start when the brain is in dev mode AND
    # binding to anything other than loopback. Dev mode relaxes:
    #   - cookies: secure=False, no __Host- prefix
    #   - HSTS header: not sent
    #   - host validation: allowed_hosts can be empty
    #   - public_url: can be plain http://
    # All four are catastrophic if the brain is reachable from the
    # internet, a LAN, Tailscale, or anywhere off-loopback. The
    # auto-promote logic above already flips to production when the
    # operator's config shape (https Z4J_PUBLIC_URL + explicit
    # Z4J_ALLOWED_HOSTS) declares production intent, so this gate
    # only fires when the operator has neither: dev mode AND a
    # non-loopback bind WITHOUT the production-shaped config.
    bind = args.host or settings.bind_host
    _LOOPBACK = ("127.0.0.1", "localhost", "[::1]", "::1")
    if settings.environment == "dev" and bind not in _LOOPBACK:
        print(  # noqa: T201
            "z4j-brain: REFUSING TO START.\n"
            "\n"
            f"  Z4J_ENVIRONMENT=dev + bind {bind!r} is unsafe:\n"
            "  cookies are not Secure, no HSTS, no host-header\n"
            "  validation. Dev defaults are localhost-only.\n"
            "\n"
            "  Pick one:\n"
            "  1. Localhost-only dev:\n"
            "       z4j serve --host 127.0.0.1\n"
            "\n"
            "  2. Public production:\n"
            "       Z4J_ENVIRONMENT=production \\\n"
            "       Z4J_PUBLIC_URL=https://tasks.example.com \\\n"
            "       Z4J_ALLOWED_HOSTS='[\"tasks.example.com\"]' \\\n"
            "       z4j serve --host 0.0.0.0\n"
            "\n"
            "  Setting BOTH Z4J_PUBLIC_URL=https://... AND\n"
            "  Z4J_ALLOWED_HOSTS auto-promotes the environment to\n"
            "  production - you don't have to set Z4J_ENVIRONMENT\n"
            "  explicitly.\n"
            "\n"
            "  See: https://z4j.dev/operations/dev-vs-production",
            file=sys.stderr,
        )
        return 2

    # Tell the operator exactly which Host headers will be accepted.
    # Without this banner the only way to learn what's whitelisted is to
    # hit the brain with a wrong Host and read the (now improved) 400
    # response, which assumes the operator can reach the brain at all.
    if settings.allowed_hosts:
        from z4j_brain.allowed_hosts import get_path as _ah_path
        from z4j_brain.allowed_hosts import read_persisted as _ah_read

        bind = args.host or settings.bind_host
        port = args.port or settings.bind_port
        joined = ", ".join(settings.allowed_hosts)
        print(  # noqa: T201
            f"z4j-brain: serving on {bind}:{port}, accepting Host headers: {joined}",
        )
        persisted = _ah_read()
        if persisted:
            print(  # noqa: T201
                f"z4j-brain: persisted from {_ah_path()}: "
                f"{', '.join(persisted)}",
            )
        print(  # noqa: T201
            f"z4j-brain: to add more, run `z4j allowed-hosts add <name>` "
            f"(persists across restarts).",
        )

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
        # Surface the active environment so operators don't have to
        # infer it from a warning further down (added v1.0.14).
        # Marked production-mode rows with the secure-default tag,
        # dev-mode rows with the relaxed-defaults tag.
        env_tag = (
            "production (TLS-required, host validation, secure cookies)"
            if settings.environment == "production"
            else "dev (loopback-only, relaxed cookies, no HSTS)"
        )
        checks.append(("environment", f"{settings.environment}  -  {env_tag}"))
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

            env_tag = (
                "(TLS-required, host validation, secure cookies)"
                if settings.environment == "production"
                else "(loopback-only, relaxed cookies, no HSTS)"
            )
            print("z4j status")  # noqa: T201
            print(f"  version             {__version__}")  # noqa: T201
            print(f"  alembic head        {rev}")  # noqa: T201
            print(f"  environment         {settings.environment}  {env_tag}")  # noqa: T201
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
