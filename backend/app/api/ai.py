from typing import Annotated

import redis
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.embeddings import get_embedder
from app.ai.llm import llm_status
from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.auth.rate_limit import get_redis
from app.core.database import get_db
from app.models import User
from app.schemas.feedback import AIQualityReport
from app.services.feedback import FeedbackService

router = APIRouter(prefix="/ai", tags=["ai quality"])

DB = Annotated[Session, Depends(get_db)]
Reader = Annotated[User, Depends(require(Permission.DASHBOARD_READ))]


class LLMStatus(BaseModel):
    provider: str
    model: str | None
    circuit: str | None  # closed | open | half_open; None when no LLM is configured
    consecutive_failures: int
    last_error: str | None


class AIStatus(BaseModel):
    llm: LLMStatus
    queue_available: bool
    embedding_model: str
    degraded: bool
    message: str | None


@router.get("/quality", response_model=AIQualityReport)
def ai_quality(db: DB, _: Reader) -> AIQualityReport:
    """How useful the AI is in practice: 👍/👎 on investigations, triage review outcomes, grounding rejections."""
    return AIQualityReport(**FeedbackService(db).quality_report())


@router.get("/status", response_model=AIStatus)
def ai_status(_: Reader, client: Annotated[redis.Redis, Depends(get_redis)]) -> AIStatus:
    """What the AI can do right now, so the UI can say so instead of failing mysteriously."""
    llm = LLMStatus(**llm_status())
    try:
        queue = bool(client.ping())
    except redis.RedisError:
        queue = False
    messages = []
    if llm.circuit in ("open", "half_open"):
        messages.append("The LLM provider is failing: write-ups and triage use the deterministic engines until it "
                        "recovers.")  # fmt: skip
    if not queue:
        messages.append("The job queue is unavailable: AI jobs run inside the API, so they take longer.")
    return AIStatus(
        llm=llm,
        queue_available=queue,
        embedding_model=get_embedder().name,
        degraded=bool(messages),
        message=" ".join(messages) or None,
    )
