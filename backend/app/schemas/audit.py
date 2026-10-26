from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator

from app.models import AuditEvent
from app.schemas.common import ORMModel, UserRef
from app.services.audit import ChainVerification, describe


class ActivityItem(ORMModel):
    """A timeline entry as analysts see it: who, what, when — no IP addresses or hashes."""

    id: int
    occurred_at: datetime
    action: str
    entity_type: str
    entity_id: str
    actor: UserRef | None
    description: str

    @classmethod
    def build(cls, event: AuditEvent) -> "ActivityItem":
        return cls.model_validate({**_fields(event), "description": describe(event)})


class AuditEventOut(ActivityItem):
    """The full row, for auditors (audit:read)."""

    changes: dict[str, Any]
    ip_address: str | None
    chain_seq: int | None
    prev_hash: str | None
    hash: str | None

    @field_validator("ip_address", mode="before")
    @classmethod
    def _ip(cls, value: object) -> str | None:
        return None if value is None else str(value)

    @classmethod
    def build(cls, event: AuditEvent) -> "AuditEventOut":
        return cls.model_validate(
            {
                **_fields(event),
                "description": describe(event),
                "changes": event.changes,
                "ip_address": event.ip_address,
                "chain_seq": event.chain_seq,
                "prev_hash": event.prev_hash,
                "hash": event.hash,
            }
        )


def _fields(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "occurred_at": event.occurred_at,
        "action": event.action,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "actor": event.actor,
    }


class ChainBreakOut(BaseModel):
    chain_seq: int
    audit_id: int
    reason: str


class ChainVerificationOut(BaseModel):
    valid: bool
    rows: int
    head_seq: int | None
    head_hash: str | None
    breaks: list[ChainBreakOut]
    verified_at: datetime

    @classmethod
    def build(cls, result: ChainVerification) -> "ChainVerificationOut":
        return cls(
            valid=result.valid,
            rows=result.rows,
            head_seq=result.head_seq,
            head_hash=result.head_hash,
            breaks=[ChainBreakOut(**vars(b)) for b in result.breaks],
            verified_at=result.verified_at,
        )
