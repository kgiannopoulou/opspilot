from typing import Annotated

import redis
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.auth.rate_limit import get_redis
from app.core.database import get_db
from app.models import BackgroundJob, User
from app.schemas.jobs import JobOut, JobsOverview, QueueStats
from app.workers.jobs import JobService

router = APIRouter(prefix="/jobs", tags=["background jobs"])

DB = Annotated[Session, Depends(get_db)]
Admin = Annotated[User, Depends(require(Permission.AUDIT_READ))]


@router.get("", response_model=JobsOverview)
def jobs_overview(
    db: DB, _: Admin, client: Annotated[redis.Redis, Depends(get_redis)], limit: int = Query(default=30, ge=1, le=200)
) -> JobsOverview:
    """Queue depth from Redis, outcomes from PostgreSQL: is the worker keeping up, and what failed?"""
    service = JobService(db, client)
    try:
        queue = QueueStats(available=True, **service.queue.stats())
    except redis.RedisError:
        queue = QueueStats(available=False)
    by_status = dict(db.execute(select(BackgroundJob.status, func.count()).group_by(BackgroundJob.status)).all())
    return JobsOverview(
        queue=queue,
        by_status={s.value: n for s, n in by_status.items()},
        recent=[JobOut.model_validate(j) for j in service.recent(limit)],
    )
