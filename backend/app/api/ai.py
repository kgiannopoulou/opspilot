from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.core.database import get_db
from app.models import User
from app.schemas.feedback import AIQualityReport
from app.services.feedback import FeedbackService

router = APIRouter(prefix="/ai", tags=["ai quality"])

DB = Annotated[Session, Depends(get_db)]
Reader = Annotated[User, Depends(require(Permission.DASHBOARD_READ))]


@router.get("/quality", response_model=AIQualityReport)
def ai_quality(db: DB, _: Reader) -> AIQualityReport:
    """How useful the AI is in practice: 👍/👎 on investigations, triage review outcomes, grounding rejections."""
    return AIQualityReport(**FeedbackService(db).quality_report())
