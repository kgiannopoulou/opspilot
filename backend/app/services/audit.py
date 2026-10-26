"""The audit trail: writing rows (record), reading them back (AuditService) and proving nobody rewrote them (verify).

Rows are chained and made append-only by the database (migration 0009); this module never updates them.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.models import AuditEvent, Incident, User

GENESIS_HASH = "0" * 64


def record(
    db: Session,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: str | int,
    changes: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    """Add an audit row to the caller's transaction — it commits or rolls back together with the change itself."""
    db.add(
        AuditEvent(
            actor_id=actor.id if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            changes=changes or {},
            ip_address=ip_address,
        )
    )


@contextlib.contextmanager
def maintenance(db: Session) -> Iterator[None]:
    """Allow UPDATE/DELETE/TRUNCATE on audit_events for the current transaction only (seed and test resets).

    Anything changed here must be followed by `rechain()`, or verification reports it — which is the point.
    """
    db.execute(text("SET LOCAL opspilot.audit_maintenance = 'on'"))
    yield


def rechain(db: Session) -> int:
    """Recompute every hash in chain order (maintenance mode only). Returns the number of rows."""
    return db.scalar(text("SELECT audit_rechain()")) or 0


@dataclass
class ChainBreak:
    chain_seq: int
    audit_id: int
    reason: str  # "modified" (row hash wrong) | "gap" (previous row missing or replaced)


@dataclass
class ChainVerification:
    valid: bool
    rows: int
    head_seq: int | None
    head_hash: str | None
    breaks: list[ChainBreak]
    verified_at: datetime


# Recompute each row's hash from its own fields and its stored prev_hash, and check prev_hash is the previous row's.
_VERIFY_SQL = text(
    """
    WITH chain AS (
        SELECT id, chain_seq, hash,
               prev_hash = coalesce(lag(hash) OVER (ORDER BY chain_seq), :genesis) AS linked,
               hash = audit_hash(prev_hash, chain_seq, occurred_at, actor_id, action, entity_type, entity_id,
                                 changes, ip_address) AS intact,
               chain_seq - coalesce(lag(chain_seq) OVER (ORDER BY chain_seq), 0) AS step
          FROM audit_events
    )
    SELECT id, chain_seq, NOT intact AS modified, (NOT linked OR step <> 1) AS gap
      FROM chain
     WHERE NOT intact OR NOT linked OR step <> 1
     ORDER BY chain_seq
     LIMIT 100
    """
)


class AuditService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def search(
        self,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        actor_id: int | None = None,
        action: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[AuditEvent], int]:
        stmt = select(AuditEvent)
        if entity_type:
            stmt = stmt.where(AuditEvent.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(AuditEvent.entity_id == entity_id)
        if actor_id is not None:
            stmt = stmt.where(AuditEvent.actor_id == actor_id)
        if action:
            # "ticket." filters a whole family; "ticket.created" one action.
            family = action.endswith(".")
            stmt = stmt.where(AuditEvent.action.startswith(action) if family else AuditEvent.action == action)
        if since:
            stmt = stmt.where(AuditEvent.occurred_at >= since)
        if until:
            stmt = stmt.where(AuditEvent.occurred_at < until)
        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = self.db.scalars(
            stmt.order_by(AuditEvent.occurred_at.desc(), AuditEvent.chain_seq.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).unique()
        return list(rows), total

    def iter_all(self, batch: int = 1000) -> Iterator[AuditEvent]:
        """Every row in chain order, for exports."""
        last = 0
        while True:
            rows = list(
                self.db.scalars(
                    select(AuditEvent).where(AuditEvent.chain_seq > last).order_by(AuditEvent.chain_seq).limit(batch)
                ).unique()
            )
            if not rows:
                return
            yield from rows
            last = rows[-1].chain_seq or last

    def incident_activity(self, incident: Incident) -> list[AuditEvent]:
        """The incident's own events plus those of every ticket linked to it, oldest first: the incident story."""
        numbers = [t.number for t in incident.tickets]
        conditions = [(AuditEvent.entity_type == "incident") & (AuditEvent.entity_id == incident.number)]
        if numbers:
            conditions.append((AuditEvent.entity_type == "ticket") & AuditEvent.entity_id.in_(numbers))
        return list(
            self.db.scalars(
                select(AuditEvent).where(or_(*conditions)).order_by(AuditEvent.occurred_at, AuditEvent.chain_seq)
            ).unique()
        )

    def entity_activity(self, entity_type: str, entity_id: str) -> list[AuditEvent]:
        return list(
            self.db.scalars(
                select(AuditEvent)
                .where(AuditEvent.entity_type == entity_type, AuditEvent.entity_id == entity_id)
                .order_by(AuditEvent.occurred_at, AuditEvent.chain_seq)
            ).unique()
        )

    def verify(self) -> ChainVerification:
        """Walk the whole chain in the database. Any edited, deleted or inserted-out-of-band row breaks it."""
        breaks = [
            ChainBreak(seq, audit_id, "modified" if modified else "gap")
            for audit_id, seq, modified, _gap in self.db.execute(_VERIFY_SQL, {"genesis": GENESIS_HASH})
        ]
        rows = self.db.scalar(select(func.count()).select_from(AuditEvent)) or 0
        head = self.db.execute(
            select(AuditEvent.chain_seq, AuditEvent.hash).order_by(AuditEvent.chain_seq.desc()).limit(1)
        ).first()
        return ChainVerification(
            valid=not breaks,
            rows=rows,
            head_seq=head[0] if head else None,
            head_hash=head[1] if head else None,
            breaks=breaks,
            verified_at=self.db.scalar(select(func.now())) or datetime.now(UTC),
        )


# -- human-readable sentences ------------------------------------------------------------------------------------


def _who(event: AuditEvent) -> str:
    return event.actor.full_name if event.actor else "OpsPilot"


def _pct(value: Any) -> str:
    return f"{round(float(value) * 100)}%" if isinstance(value, int | float) else "?"


def describe(event: AuditEvent) -> str:
    """One sentence for the activity timeline, e.g. "Maria Papadopoulou assigned TCK-2026-00004 to Alex Chen"."""
    c, who, eid = event.changes or {}, _who(event), event.entity_id
    match event.action:
        case "ticket.created":
            return f"Ticket {eid} created by {who}"
        case "ticket.ai_triage_started":
            return f"AI classification started for {eid}"
        case "ticket.ai_classified":
            confidence = (c.get("data") or {}).get("confidence")
            return f"AI predicted {c.get('to_value')} for {eid} ({_pct(confidence)} confidence)"
        case "ticket.assigned":
            target = c.get("to_value")
            return f"{who} assigned {eid} to {target}" if target else f"{who} unassigned {eid}"
        case "ticket.status_changed":
            return f"{who} moved {eid} {c.get('from_value')} → {c.get('to_value')}"
        case "ticket.priority_changed":
            return f"{who} changed priority of {eid} {c.get('from_value')} → {c.get('to_value')}"
        case "ticket.category_changed":
            return f"{who} set category of {eid} to {c.get('to_value')}"
        case "ticket.updated":
            data = c.get("data") or {}
            if service := data.get("service"):
                return f"{who} changed service on {eid} → {service.get('to') or 'none'}"
            return f"{who} edited {', '.join(data.get('fields', [])) or 'fields'} on {eid}"
        case "ticket.commented":
            return f"{who} commented on {eid}"
        case "ticket.linked_to_incident":
            return f"{who} linked {eid} to {c.get('to_value')}"
        case "ticket.ai_suggestion_reviewed":
            verdict = {"ACCEPTED": "accepted", "MODIFIED": "corrected", "REJECTED": "rejected"}.get(
                c.get("to_value", ""), "reviewed"
            )
            return f"{who} {verdict} the AI triage suggestion for {eid}"
        case "incident.declared":
            return f"Incident {eid} declared by {who} ({c.get('severity')})"
        case "incident.ticket_linked":
            return f"{who} linked {c.get('ticket')} to {eid}"
        case "incident.investigation_requested":
            return f"{who} requested an AI investigation"
        case "incident.investigation_started":
            return "AI investigation started"
        case "incident.investigated":
            return f"AI investigation generated: {c.get('hypothesis')} ({_pct(c.get('confidence'))} confidence)"
        case "incident.investigation_failed":
            return f"AI investigation failed ({c.get('error', 'unknown error')})"
        case "incident.ai_root_cause_accepted":
            return f"{who} accepted the AI root cause from investigation #{c.get('investigation_id')}"
        case "incident.status_changed":
            if c.get("to") == "RESOLVED":
                return f"Incident resolved by {who}"
            return f"{who} moved the incident {c.get('from')} → {c.get('to')}"
        case "query.executed":
            return f"{who} ran investigation query #{c.get('query_id')} ({c.get('rows')} rows)"
        case "query.rejected":
            return f"{who}'s edited query #{c.get('query_id')} was refused by the SQL validator"
        case "ai.feedback_submitted":
            verdict = "useful" if c.get("rating") == 1 else f"incorrect ({c.get('reason')})"
            return f"{who} rated investigation #{c.get('investigation_id')} {verdict}"
        case "ai.feedback_withdrawn":
            return f"{who} withdrew their rating of investigation #{c.get('investigation_id')}"
        case "auth.login_succeeded":
            return f"{who} signed in"
        case "auth.login_failed":
            return f"Failed sign-in for {eid}"
        case "auth.refresh_reuse_detected":
            return f"Refresh-token reuse detected for {who}: all sessions revoked"
        case "auth.logout":
            return f"{who} signed out"
        case "user.created":
            return f"{who} created user {c.get('email')} ({c.get('role')})"
        case "user.updated":
            return f"{who} updated user #{eid}"
        case "user.deleted":
            return f"{who} deactivated user {c.get('email')}"
        case _:
            return f"{event.action} on {event.entity_type} {eid} by {who}"
