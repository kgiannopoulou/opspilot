from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.enums import ClassificationStatus, Priority, TicketCategory
from app.schemas.common import ORMModel, ServiceRef, UserRef


class ClassificationOut(ORMModel):
    id: int
    engine: str
    category: TicketCategory
    priority: Priority
    service: ServiceRef | None
    confidence: float
    rationale: str
    signals: dict[str, Any]
    status: ClassificationStatus
    final_category: TicketCategory | None
    final_priority: Priority | None
    final_service: ServiceRef | None
    review_note: str | None
    reviewed_by: UserRef | None
    reviewed_at: datetime | None
    created_at: datetime


class ClassificationReview(BaseModel):
    action: Literal["accept", "modify", "reject"]
    # Only used with "modify"; anything left out keeps the AI's value.
    category: TicketCategory | None = None
    priority: Priority | None = None
    service: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=1_000)


class ClassificationMetrics(BaseModel):
    total: int
    by_status: dict[str, int]
    acceptance_rate: float | None
    corrections: dict[str, int]
