from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import QueryRisk, QueryRunStatus
from app.schemas.common import ORMModel, UserRef


class QueryRunOut(ORMModel):
    id: int
    sql: str
    edited: bool
    status: QueryRunStatus
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    duration_ms: int | None
    error: str | None
    executed_by: UserRef | None
    executed_at: datetime


class QueryOut(ORMModel):
    id: int
    position: int
    title: str
    purpose: str
    sql: str
    safe_sql: str | None
    source: str
    risk: QueryRisk
    violations: list[str]
    tables: list[str]
    last_run: QueryRunOut | None = None


class RunQuery(BaseModel):
    sql: str | None = Field(default=None, max_length=5_000, description="Edited SQL; omit to run the proposal as is")


class SqlCheck(BaseModel):
    sql: str = Field(max_length=5_000)


class SqlVerdict(BaseModel):
    ok: bool
    risk: QueryRisk
    safe_sql: str | None
    violations: list[str]
    tables: list[str]
