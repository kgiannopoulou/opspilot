import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.enums import JobStatus
from app.schemas.common import ORMModel


class JobOut(ORMModel):
    id: uuid.UUID
    type: str
    status: JobStatus
    entity_type: str
    entity_id: str
    payload: dict[str, Any]
    attempts: int
    error: str | None
    worker: str | None
    enqueued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class QueueStats(BaseModel):
    available: bool
    waiting: int = 0
    in_progress: int = 0
    delayed: int = 0
    dead: int = 0


class JobsOverview(BaseModel):
    queue: QueueStats
    by_status: dict[str, int]
    recent: list[JobOut]
