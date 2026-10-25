from typing import Annotated

from fastapi import APIRouter, Depends

from app.ai.sql_guard import validate
from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.models import User
from app.models.enums import QueryRisk
from app.schemas.queries import SqlCheck, SqlVerdict

router = APIRouter(prefix="/sql", tags=["investigation queries"])

Investigator = Annotated[User, Depends(require(Permission.INCIDENTS_INVESTIGATE))]


@router.post("/validate", response_model=SqlVerdict)
def validate_sql(data: SqlCheck, _: Investigator) -> SqlVerdict:
    """Dry-run the safety checks (no execution): lets the UI show the verdict while an engineer edits a query."""
    verdict = validate(data.sql)
    return SqlVerdict(
        ok=verdict.ok,
        risk=QueryRisk.READ_ONLY if verdict.ok else QueryRisk.BLOCKED,
        safe_sql=verdict.safe_sql,
        violations=verdict.violations,
        tables=verdict.tables,
    )
