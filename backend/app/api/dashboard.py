from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.core.cache import cached_json
from app.core.database import get_db
from app.models import Incident, User
from app.models.enums import IncidentStatus, Severity
from app.repositories.dashboard import DashboardRepository
from app.schemas.incident import IncidentSummary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

DB = Annotated[Session, Depends(get_db)]
Reader = Annotated[User, Depends(require(Permission.DASHBOARD_READ))]

SEVERITY_RANK = {s: i for i, s in enumerate(Severity)}


def build_summary(db: Session, now: datetime) -> dict[str, Any]:
    repo = DashboardRepository(db)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since = (now - timedelta(days=29)).date()

    active = db.scalars(select(Incident).where(Incident.status != IncidentStatus.RESOLVED)).unique().all()
    ticket_counts = repo.ticket_count_per_incident()
    active_sorted = sorted(active, key=lambda i: (SEVERITY_RANK[i.severity], i.started_at))

    return {
        "generated_at": now.isoformat(),
        "active_incidents": repo.active_incidents_by_severity(),
        "resolved_last_30d": repo.resolved_count(now - timedelta(days=30)),
        "mttr_minutes": repo.mttr_minutes(now - timedelta(days=30), now),
        "mttr_minutes_previous": repo.mttr_minutes(now - timedelta(days=60), now - timedelta(days=30)),
        "tickets": repo.ticket_counts(today),
        "incident_trend": repo.incident_trend(since),
        "ticket_volume": repo.ticket_volume(since),
        "services": repo.service_health(),
        "active": [
            IncidentSummary.build(i, ticket_counts.get(i.id, 0), now).model_dump(mode="json") for i in active_sorted
        ],
    }


@router.get("/summary")
def summary(db: DB, _: Reader) -> dict[str, Any]:
    """Everything the main dashboard renders, in one round trip. Cached for 15 s — it is polled by every viewer."""
    return cached_json("dashboard:summary:v1", 15, lambda: build_summary(db, datetime.now(UTC)))
