from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.core.database import get_db
from app.models import Incident, User
from app.models.enums import IncidentStatus, Severity
from app.repositories.dashboard import DashboardRepository
from app.schemas.common import Page
from app.schemas.incident import IncidentSummary

router = APIRouter(prefix="/incidents", tags=["incidents"])

DB = Annotated[Session, Depends(get_db)]
Reader = Annotated[User, Depends(require(Permission.INCIDENTS_READ))]


@router.get("", response_model=Page[IncidentSummary])
def list_incidents(
    db: DB,
    _: Reader,
    state: Literal["active", "resolved", "all"] = "all",
    severity: list[Severity] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> Page[IncidentSummary]:
    stmt = select(Incident)
    if state == "active":
        stmt = stmt.where(Incident.status != IncidentStatus.RESOLVED)
    elif state == "resolved":
        stmt = stmt.where(Incident.status == IncidentStatus.RESOLVED)
    if severity:
        stmt = stmt.where(Incident.severity.in_(severity))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    # Active incidents first, then most recent.
    rows = db.scalars(
        stmt.order_by((Incident.status == IncidentStatus.RESOLVED).asc(), Incident.started_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).unique()
    counts = DashboardRepository(db).ticket_count_per_incident()
    now = datetime.now(UTC)
    return Page(
        items=[IncidentSummary.build(i, counts.get(i.id, 0), now) for i in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
