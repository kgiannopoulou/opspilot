from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.enums import FeedbackReason
from app.schemas.classification import ClassificationMetrics
from app.schemas.common import ORMModel, UserRef


class FeedbackIn(BaseModel):
    rating: Literal[1, -1] = Field(description="1 = useful, -1 = incorrect")
    reason: FeedbackReason | None = Field(default=None, description="Required when rating is -1")
    comment: str | None = Field(default=None, max_length=2_000)


class FeedbackOut(ORMModel):
    id: int
    investigation_id: int
    rating: int
    reason: FeedbackReason | None
    comment: str | None
    user: UserRef
    created_at: datetime
    updated_at: datetime


class FeedbackSummary(BaseModel):
    mine: FeedbackOut | None
    useful: int
    incorrect: int
    reasons: dict[str, int]


class NegativeFeedback(BaseModel):
    incident: str
    investigation_id: int
    hypothesis: str | None
    reason: str | None
    comment: str | None
    user: str
    at: datetime


class InvestigationQuality(BaseModel):
    completed: int
    failed: int
    rated: int
    votes: int
    useful: int
    incorrect: int
    useful_rate: float | None
    reasons: dict[str, int]
    by_engine: dict[str, dict[str, Any]]
    by_hypothesis: dict[str, dict[str, Any]]
    grounding_passed: int
    llm_drafts: int
    llm_drafts_rejected: int


class AIQualityReport(BaseModel):
    investigations: InvestigationQuality
    triage: ClassificationMetrics
    recent_negative: list[NegativeFeedback]
