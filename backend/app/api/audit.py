import csv
import io
import json
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.core.database import get_db
from app.models import User
from app.schemas.audit import AuditEventOut, ChainVerificationOut
from app.schemas.common import Page
from app.services import audit
from app.services.audit import AuditService

router = APIRouter(prefix="/audit", tags=["audit"])

DB = Annotated[Session, Depends(get_db)]
Auditor = Annotated[User, Depends(require(Permission.AUDIT_READ))]

EXPORT_COLUMNS = ["chain_seq", "occurred_at", "actor", "action", "entity_type", "entity_id", "changes", "ip_address",
                  "prev_hash", "hash", "description"]  # fmt: skip


@router.get("", response_model=Page[AuditEventOut])
def search_audit(
    db: DB,
    _: Auditor,
    entity_type: str | None = Query(default=None, max_length=32),
    entity_id: str | None = Query(default=None, max_length=64),
    actor_id: int | None = None,
    action: str | None = Query(default=None, max_length=64, description='Exact action, or a prefix ending in "."'),
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[AuditEventOut]:
    rows, total = AuditService(db).search(
        entity_type=entity_type, entity_id=entity_id, actor_id=actor_id, action=action, since=since, until=until,
        page=page, page_size=page_size,
    )  # fmt: skip
    return Page(items=[AuditEventOut.build(r) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/verify", response_model=ChainVerificationOut)
def verify_audit_chain(db: DB, user: Auditor) -> ChainVerificationOut:
    """Recompute the hash chain. Record `head_hash` somewhere else (ticket, chat, WORM bucket) to anchor it."""
    result = AuditService(db).verify()
    audit.record(db, user, "audit.verified", "audit", result.head_seq or 0,
                 {"valid": result.valid, "rows": result.rows, "breaks": len(result.breaks)})  # fmt: skip
    db.commit()
    return ChainVerificationOut.build(result)


@router.get("/export.csv")
def export_audit(db: DB, user: Auditor) -> StreamingResponse:
    """The whole trail in chain order, with hashes, so it can be archived and re-verified offline."""
    audit.record(db, user, "audit.exported", "audit", "csv")
    db.commit()

    def rows() -> Iterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(EXPORT_COLUMNS)
        for e in AuditService(db).iter_all():
            out = AuditEventOut.build(e)
            writer.writerow([
                e.chain_seq, e.occurred_at.isoformat(), out.actor.email if out.actor else "", e.action, e.entity_type,
                e.entity_id, json.dumps(e.changes, sort_keys=True), out.ip_address or "", e.prev_hash, e.hash,
                out.description,
            ])  # fmt: skip
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()

    return StreamingResponse(rows(), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="opspilot-audit.csv"'})  # fmt: skip
