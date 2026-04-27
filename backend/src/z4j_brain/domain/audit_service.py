"""Append-only audit log service with per-row HMAC tamper evidence.

Every privileged action goes through :meth:`AuditService.record`.
The service:

1. Builds a canonical JSON representation of the row's content
   (every field except ``id``, ``row_hmac``, and ``occurred_at``,
   plus ``occurred_at`` as an ISO timestamp string).
2. Computes ``HMAC-SHA256(settings.secret, canonical)``.
3. Inserts the row via :class:`AuditLogRepository`.

The verifier (:meth:`verify_row`) recomputes the HMAC and
constant-time-compares. Used by the ``z4j-brain audit verify``
CLI command and by tests.

Combined with the database append-only trigger, this gives us
tamper evidence for any party who does NOT also hold the master
secret. A privileged DBA who DOES hold the secret can still
forge rows - that scenario is out of scope for the brain
(addressed by operational controls: secret in env, not on disk).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from z4j_brain.persistence.models import AuditLog
    from z4j_brain.persistence.repositories import AuditLogRepository
    from z4j_brain.settings import Settings


#: Canonical-form fields, in stable order. Adding fields = bumping
#: the canonicalisation version (rotate ``_HMAC_VERSION``).
_CANONICAL_FIELDS: tuple[str, ...] = (
    "version",
    "id",
    "action",
    "target_type",
    "target_id",
    "result",
    "outcome",
    "event_id",
    "user_id",
    "project_id",
    "source_ip",
    "user_agent",
    "metadata",
    "occurred_at",
    "prev_row_hmac",
)

#: Bumped only when the canonical-fields list changes shape.
#:
#: v3 (current) - adds ``prev_row_hmac`` to the HMAC input so
#: consecutive rows form a chain. Deleting any row breaks the
#: next row's ``prev_row_hmac`` anchor, which ``verify_chain``
#: detects. Without this a DBA with raw DELETE could erase rows
#: without leaving evidence (audit finding A8).
#:
#: v2 - added the row ``id`` to the HMAC input. Prevents row-
#: clone by an attacker with raw write access.
#:
#: Verification falls back through v3 → v2 → v1 so upgrades do
#: not invalidate the existing chain. Rows written before v3
#: will have ``prev_row_hmac IS NULL`` and verify at v2.
_HMAC_VERSION: int = 3

# Drift guard - assert at import time that v2 fields stay in sync.
assert "id" in _CANONICAL_FIELDS, "audit canonical drift: id missing from v2"


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """Plain-data view of an audit row, ready for write or verify.

    ``id`` is part of the HMAC input as of canonical version 2.
    For freshly-recorded rows the service mints the UUID up-front
    so the HMAC and the persisted row carry the same value.
    """

    id: uuid.UUID | None
    action: str
    target_type: str
    target_id: str | None
    result: str
    outcome: str | None
    event_id: uuid.UUID | None
    user_id: uuid.UUID | None
    project_id: uuid.UUID | None
    source_ip: str | None
    user_agent: str | None
    metadata: dict[str, Any]
    occurred_at: datetime
    # v3: the prior row's ``row_hmac`` at the moment THIS row was
    # written. ``None`` for the very first row (genesis) and for
    # pre-v3 rows that predate chaining.
    prev_row_hmac: str | None = None


class AuditService:
    """Single entry point for writing the audit log.

    The service holds:
    - the master secret (for HMAC computation)
    - the repository (for the actual INSERT)

    It does NOT hold a session - callers pass the repository in
    per-request, so the audit row participates in the caller's
    transaction. This is intentional: an audit row that "would
    have been written but the caller's transaction rolled back"
    is the wrong outcome for both compliance and debugging.
    """

    __slots__ = ("_secret",)

    def __init__(self, settings: Settings) -> None:
        self._secret: bytes = settings.secret.get_secret_value().encode("utf-8")

    async def record(
        self,
        repo: AuditLogRepository,
        *,
        action: str,
        target_type: str,
        target_id: str | None = None,
        result: str = "success",
        outcome: str | None = None,
        event_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        source_ip: str | None = None,
        user_agent: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Append one row to the audit log inside the caller's transaction.

        ``outcome`` defaults to ``"allow"`` when ``result == "success"``,
        ``"deny"`` when ``result == "failed"``, and ``"error"``
        otherwise. The caller can override.
        """
        # Mint the row id up-front so it can be folded into the HMAC
        # input. Without this, an attacker with raw write access
        # could clone the row payload + HMAC to create an
        # undetectable duplicate. The id is unique per row so each
        # signature binds to exactly one persisted row.
        row_id = uuid.uuid4()
        # v3 chain: fetch the prior row's hmac so we can fold it
        # into this row's input. A subsequent DELETE of any row
        # then leaves the next row's ``prev_row_hmac`` referencing
        # a prior row whose hmac no longer matches - detectable by
        # ``verify_chain``. Audit finding A8.
        prev_row_hmac = await repo.get_latest_row_hmac()
        entry = AuditEntry(
            id=row_id,
            action=action[:80],
            target_type=target_type[:40],
            target_id=target_id[:200] if target_id else None,
            result=result[:20],
            outcome=outcome or self._default_outcome(result),
            event_id=event_id,
            user_id=user_id,
            project_id=project_id,
            source_ip=source_ip,
            user_agent=(user_agent[:1024] if user_agent else None),
            metadata=metadata or {},
            occurred_at=datetime.now(UTC),
            prev_row_hmac=prev_row_hmac,
        )
        row_hmac = self._compute_hmac(entry, version=_HMAC_VERSION)
        return await repo.insert(
            id=row_id,
            action=entry.action,
            target_type=entry.target_type,
            target_id=entry.target_id,
            result=entry.result,
            outcome=entry.outcome,
            event_id=entry.event_id,
            user_id=entry.user_id,
            project_id=entry.project_id,
            source_ip=entry.source_ip,
            user_agent=entry.user_agent,
            metadata=entry.metadata,
            row_hmac=row_hmac,
            prev_row_hmac=prev_row_hmac,
            occurred_at=entry.occurred_at,
        )

    def verify_row(self, row: AuditLog) -> bool:
        """Recompute the HMAC for ``row`` and compare it constant-time.

        Returns False on missing ``row_hmac`` or tampered field.
        Tries the current canonical version (v3 - chain) first,
        falls back to v2 (id only) and v1 (original) so rows
        written before the upgrade stay verifiable.
        """
        stored = row.row_hmac
        if not stored:
            return False
        entry = AuditEntry(
            id=row.id,
            action=row.action,
            target_type=row.target_type,
            target_id=row.target_id,
            result=row.result,
            outcome=row.outcome,
            event_id=row.event_id,
            user_id=row.user_id,
            project_id=row.project_id,
            source_ip=row.source_ip,
            user_agent=row.user_agent,
            metadata=row.audit_metadata,
            occurred_at=row.occurred_at,
            prev_row_hmac=getattr(row, "prev_row_hmac", None),
        )
        for version in (_HMAC_VERSION, 2, 1):
            recomputed = self._compute_hmac(entry, version=version)
            if len(recomputed) == len(stored) and hmac.compare_digest(
                recomputed, stored,
            ):
                return True
        return False

    def verify_chain(
        self, rows: "list[AuditLog]",
    ) -> tuple[bool, list[str]]:
        """Walk a sequence of rows and verify the HMAC chain.

        Expects rows ordered by insert order (``id`` UUIDv7 or
        ``occurred_at`` ascending). Returns ``(ok, reasons)``
        where ``reasons`` is a list of human-readable descriptions
        of any chain break - "row X: prev_row_hmac mismatch (saw
        Y, expected Z)" / "row X: bad row_hmac" / "gap after row X".

        A clean chain returns ``(True, [])``. A single deleted row
        produces a visible mismatch at the next chained row -
        that's the tamper-evidence audit A8 asked for.

        Rows that predate v3 (``prev_row_hmac is None`` AND the
        row verifies at v1 or v2) are treated as pre-chain
        baseline - not checked for linkage, only integrity.
        """
        reasons: list[str] = []
        prev_hmac: str | None = None
        saw_v3 = False
        for row in rows:
            if not self.verify_row(row):
                reasons.append(
                    f"row {row.id}: bad row_hmac (tampered field "
                    f"or missing hmac)",
                )
                continue
            # Chain check - only applies once we've seen any v3
            # row, since pre-v3 rows are allowed to carry None.
            if getattr(row, "prev_row_hmac", None) is not None:
                saw_v3 = True
            if saw_v3 and prev_hmac is not None:
                expected = prev_hmac
                actual = getattr(row, "prev_row_hmac", None)
                if actual != expected:
                    reasons.append(
                        f"row {row.id}: prev_row_hmac mismatch "
                        f"(saw {actual[:12] if actual else None}, "
                        f"expected {expected[:12]}). Likely a "
                        f"deleted row between this and the prior.",
                    )
            prev_hmac = row.row_hmac
        return (len(reasons) == 0, reasons)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _compute_hmac(self, entry: AuditEntry, *, version: int) -> str:
        """Canonical → HMAC-SHA256 hex digest for the requested version."""
        canonical = self._canonicalize(entry, version=version)
        digest = hmac.new(
            self._secret,
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return digest

    @staticmethod
    def _canonicalize(entry: AuditEntry, *, version: int) -> str:
        """Render the canonical form for HMAC input at one version.

        Stable JSON: sorted keys at every level, ISO-8601 UTC for
        the timestamp, ``str()`` for UUIDs, ``None`` for missing
        optionals. Version is part of the payload so collisions
        across versions are impossible.

        v2 (current) - adds the row ``id`` so each persisted row
        has a unique signature even if the rest of the fields are
        identical to another row.
        v1 - the original layout without ``id``. Kept for verifying
        rows written before the upgrade.
        """
        payload: dict[str, Any] = {
            "version": version,
            "action": entry.action,
            "target_type": entry.target_type,
            "target_id": entry.target_id,
            "result": entry.result,
            "outcome": entry.outcome,
            "event_id": str(entry.event_id) if entry.event_id else None,
            "user_id": str(entry.user_id) if entry.user_id else None,
            "project_id": str(entry.project_id) if entry.project_id else None,
            "source_ip": entry.source_ip,
            "user_agent": entry.user_agent,
            "metadata": entry.metadata,
            "occurred_at": (
                entry.occurred_at.astimezone(UTC).isoformat(timespec="microseconds")
            ),
        }
        if version >= 2:
            payload["id"] = str(entry.id) if entry.id else None
        if version >= 3:
            payload["prev_row_hmac"] = entry.prev_row_hmac
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )

    @staticmethod
    def _default_outcome(result: str) -> str:
        if result == "success":
            return "allow"
        if result == "failed":
            return "deny"
        return "error"


__all__ = ["AuditEntry", "AuditService"]
