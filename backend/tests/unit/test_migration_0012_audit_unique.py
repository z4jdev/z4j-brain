"""Regression tests for migration ``2026_04_28_0012_audit_unique``.

The 1.1.0 release of this migration crashed mid-flight on populated
DBs that had pre-existing duplicate ``prev_row_hmac`` rows
(``sqlite3.IntegrityError: UNIQUE constraint failed``). The 1.1.1
fix adds a pre-flight check that refuses cleanly with
``alembic.util.CommandError`` and a precise remediation message
pointing operators at the ``z4j audit fork-cleanup`` CLI.

These tests exercise both paths:

1. **Clean populated DB** - migration applies, partial UNIQUE
   index gets created, no errors.
2. **Populated DB with chain forks** - migration refuses cleanly
   with a CommandError that mentions ``audit fork-cleanup``;
   the index is NOT created; alembic_version stays at the
   prior head.
3. **Post-cleanup re-run** - after manually quarantining the
   forks (the same logic ``z4j audit fork-cleanup`` ships),
   the migration applies on the second attempt.

These tests guard against regressing the Django-grade migration
contract: never crash on real production data; always offer a
documented remediation path; never silently mutate audit data.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.util import CommandError
from sqlalchemy import create_engine, inspect, text


@pytest.fixture
def alembic_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    db_path = tmp_path / "brain.sqlite"
    sync_url = f"sqlite:///{db_path}"

    monkeypatch.setenv("Z4J_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("Z4J_SECRET", "x" * 64)
    monkeypatch.setenv("Z4J_SESSION_SECRET", "y" * 64)
    monkeypatch.setenv("Z4J_ENVIRONMENT", "dev")

    backend_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option(
        "script_location",
        str(backend_root / "src" / "z4j_brain" / "migrations"),
    )
    cfg.attributes["test_sync_url"] = sync_url
    return cfg


_PRIOR_HEAD = "2026_04_27_0011_sched_rate"
_TARGET_HEAD = "2026_04_28_0012_audit_unique"
_INDEX_NAME = "ux_audit_log_prev_row_hmac"


def _seed_audit_rows(engine, rows: list[dict[str, object]]) -> None:
    """Insert rows into audit_log via raw SQL.

    We bypass the ORM and AuditService so the test can deliberately
    craft fork rows (duplicate prev_row_hmac) that AuditService
    would refuse to write.
    """
    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, project_id, user_id, action, target_type, "
                    " target_id, result, metadata, source_ip, user_agent, "
                    " occurred_at, prev_row_hmac, row_hmac) "
                    "VALUES (:id, :project_id, :user_id, :action, "
                    " :target_type, :target_id, :result, :metadata, "
                    " :source_ip, :user_agent, :occurred_at, "
                    " :prev_row_hmac, :row_hmac)"
                ),
                row,
            )


def _audit_row(
    *,
    row_hmac: str,
    prev_row_hmac: str | None,
    action: str = "test.action",
) -> dict[str, object]:
    return {
        "id": uuid.uuid4().hex,
        "project_id": None,
        "user_id": None,
        "action": action,
        "target_type": "test",
        "target_id": None,
        "result": "success",
        "metadata": "{}",
        "source_ip": None,
        "user_agent": None,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "prev_row_hmac": prev_row_hmac,
        "row_hmac": row_hmac,
    }


def test_migration_0012_applies_on_clean_populated_db(
    alembic_cfg: Config,
) -> None:
    """Migration applies cleanly when audit_log has rows but no forks.

    This is the common upgrade path: an operator on 1.0.x with a
    healthy chain (no forks) upgrades to 1.1.x. The partial UNIQUE
    index gets created and the migration completes.
    """
    command.upgrade(alembic_cfg, _PRIOR_HEAD)

    sync_url = alembic_cfg.attributes["test_sync_url"]
    engine = create_engine(sync_url)
    try:
        # Seed a small valid chain: genesis (NULL prev) + 2 linked.
        _seed_audit_rows(
            engine,
            [
                _audit_row(row_hmac="a" * 64, prev_row_hmac=None),
                _audit_row(row_hmac="b" * 64, prev_row_hmac="a" * 64),
                _audit_row(row_hmac="c" * 64, prev_row_hmac="b" * 64),
            ],
        )

        command.upgrade(alembic_cfg, _TARGET_HEAD)

        # Verify the partial UNIQUE index now exists.
        inspector = inspect(engine)
        indexes = {ix["name"] for ix in inspector.get_indexes("audit_log")}
        assert _INDEX_NAME in indexes
    finally:
        engine.dispose()


def test_migration_0012_refuses_cleanly_on_chain_forks(
    alembic_cfg: Config,
) -> None:
    """Migration raises CommandError when forks exist; index NOT created.

    This is the regression test for the 1.1.0 incident. Before 1.1.1
    this raised ``sqlite3.IntegrityError`` from inside CREATE INDEX,
    which alembic surfaces as an opaque crash. After 1.1.1 the
    pre-flight check raises CommandError with a clear remediation
    pointing at ``z4j audit fork-cleanup``.
    """
    command.upgrade(alembic_cfg, _PRIOR_HEAD)

    sync_url = alembic_cfg.attributes["test_sync_url"]
    engine = create_engine(sync_url)
    try:
        # Seed a fork: two rows share the same prev_row_hmac.
        # Plus a third in a separate fork (different prev_hash).
        shared_prev_a = "a" * 64
        shared_prev_b = "b" * 64
        _seed_audit_rows(
            engine,
            [
                _audit_row(row_hmac="01" * 32, prev_row_hmac=None),
                _audit_row(row_hmac="02" * 32, prev_row_hmac=shared_prev_a),
                _audit_row(row_hmac="03" * 32, prev_row_hmac=shared_prev_a),
                _audit_row(row_hmac="04" * 32, prev_row_hmac=shared_prev_b),
                _audit_row(row_hmac="05" * 32, prev_row_hmac=shared_prev_b),
            ],
        )

        with pytest.raises(CommandError) as exc_info:
            command.upgrade(alembic_cfg, _TARGET_HEAD)

        msg = str(exc_info.value)
        # Remediation must mention the new CLI command.
        assert "z4j audit fork-cleanup" in msg
        # Must explain what's blocking.
        assert "duplicate prev_row_hmac" in msg
        # Must NOT be a raw IntegrityError stack.
        assert "IntegrityError" not in msg

        # Index must NOT have been created (no half-applied state).
        inspector = inspect(engine)
        indexes = {ix["name"] for ix in inspector.get_indexes("audit_log")}
        assert _INDEX_NAME not in indexes

        # alembic_version must still be at the prior head, NOT the target.
        with engine.begin() as conn:
            current = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert current == _PRIOR_HEAD
    finally:
        engine.dispose()


def test_migration_0012_applies_after_manual_fork_cleanup(
    alembic_cfg: Config,
) -> None:
    """After quarantining forks (the operator workflow), migration applies.

    This proves the documented recovery path actually recovers:
    operator hits the CommandError, runs ``z4j audit fork-cleanup``
    (or the equivalent SQL), re-runs ``z4j serve``. Migration now
    completes and the chain index exists.
    """
    command.upgrade(alembic_cfg, _PRIOR_HEAD)

    sync_url = alembic_cfg.attributes["test_sync_url"]
    engine = create_engine(sync_url)
    try:
        # Seed forks first.
        _seed_audit_rows(
            engine,
            [
                _audit_row(row_hmac="01" * 32, prev_row_hmac=None),
                _audit_row(row_hmac="02" * 32, prev_row_hmac="a" * 64),
                _audit_row(row_hmac="03" * 32, prev_row_hmac="a" * 64),
            ],
        )

        # Confirm the migration would refuse.
        with pytest.raises(CommandError):
            command.upgrade(alembic_cfg, _TARGET_HEAD)

        # Operator runs fork-cleanup (the same SQL the CLI ships):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS audit_log_legacy_forks "
                    "AS SELECT * FROM audit_log WHERE 1=0"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO audit_log_legacy_forks "
                    "SELECT * FROM audit_log "
                    "WHERE prev_row_hmac IS NOT NULL "
                    "  AND prev_row_hmac IN ("
                    "    SELECT prev_row_hmac FROM audit_log "
                    "    WHERE prev_row_hmac IS NOT NULL "
                    "    GROUP BY prev_row_hmac "
                    "    HAVING COUNT(*) > 1"
                    "  )"
                    "  AND id NOT IN ("
                    "    SELECT MIN(id) FROM audit_log "
                    "    WHERE prev_row_hmac IS NOT NULL "
                    "    GROUP BY prev_row_hmac"
                    "  )"
                )
            )
            conn.execute(
                text(
                    "DELETE FROM audit_log "
                    "WHERE id IN (SELECT id FROM audit_log_legacy_forks)"
                )
            )

        # Now the migration should apply.
        command.upgrade(alembic_cfg, _TARGET_HEAD)

        inspector = inspect(engine)
        indexes = {ix["name"] for ix in inspector.get_indexes("audit_log")}
        assert _INDEX_NAME in indexes

        with engine.begin() as conn:
            current = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert current == _TARGET_HEAD

        # Forensic preservation: the legacy table still has the fork rows.
        with engine.begin() as conn:
            legacy_count = conn.execute(
                text("SELECT COUNT(*) FROM audit_log_legacy_forks")
            ).scalar_one()
        assert legacy_count == 1
    finally:
        engine.dispose()
