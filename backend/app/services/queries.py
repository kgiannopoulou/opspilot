"""Run a proposed investigation query — the execution half of the SQL safety design.

Layers, outermost first (each one alone would stop a destructive query; together they also stop slow or nosy ones):

    1. permission       only `incidents:investigate` (engineers, admins) may run queries; every run is audited
    2. validator        sql_guard.validate() again at run time — stored verdicts and edited SQL are never trusted
    3. read-only txn    SET TRANSACTION READ ONLY: PostgreSQL itself refuses any write
    4. least privilege  SET LOCAL ROLE opspilot_readonly: can read the ShopStream schema, nothing of OpsPilot's
    5. time & size      statement_timeout and lock_timeout; at most MAX_ROWS rows fetched, 100 kept
    6. no side effects  the transaction is always rolled back, on a separate connection from the request's session
"""

import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.ai.sql_guard import MAX_ROWS, validate
from app.core.errors import NotFoundError
from app.models import AIInvestigation, Incident, InvestigationQuery, QueryRun, User
from app.models.enums import QueryRunStatus
from app.services import audit

READONLY_ROLE = "opspilot_readonly"
STATEMENT_TIMEOUT_MS = 3_000
KEEP_ROWS = 100


def _json(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    return str(value)


class QueryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, incident: Incident, investigation_id: int, query_id: int) -> InvestigationQuery:
        query = self.db.scalar(
            select(InvestigationQuery)
            .join(AIInvestigation, AIInvestigation.id == InvestigationQuery.investigation_id)
            .where(
                InvestigationQuery.id == query_id,
                InvestigationQuery.investigation_id == investigation_id,
                AIInvestigation.incident_id == incident.id,
            )
        )
        if query is None:
            raise NotFoundError(f"query {query_id} not found in investigation {investigation_id}")
        return query

    def run(self, incident: Incident, query: InvestigationQuery, actor: User, sql: str | None = None) -> QueryRun:
        text = (sql or query.sql).strip()
        edited = sql is not None and text != query.sql.strip()
        verdict = validate(text)
        run = QueryRun(query_id=query.id, executed_by_id=actor.id, sql=text, edited=edited)
        if not verdict.ok:
            run.status, run.error = QueryRunStatus.REJECTED, "; ".join(verdict.violations)
        else:
            run.sql = verdict.safe_sql or text
            self._execute(run)
        self.db.add(run)
        self.db.flush()
        audit.record(
            self.db,
            actor,
            "query.executed" if run.status != QueryRunStatus.REJECTED else "query.rejected",
            "incident",
            incident.number,
            {"query_id": query.id, "run_id": run.id, "status": run.status.value, "edited": edited,
             "rows": run.row_count, "sql": run.sql[:2000]},
        )  # fmt: skip
        self.db.commit()
        self.db.refresh(run)
        return run

    def _execute(self, run: QueryRun) -> None:
        started = time.perf_counter()
        engine = self.db.get_bind()
        assert isinstance(engine, Engine)
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                conn.exec_driver_sql(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
                conn.exec_driver_sql("SET LOCAL lock_timeout = 1000")
                conn.exec_driver_sql(f"SET LOCAL ROLE {READONLY_ROLE}")
                conn.exec_driver_sql("SET LOCAL search_path = shopstream")
                raw = conn.connection.driver_connection
                assert raw is not None
                cursor = raw.cursor()  # raw cursor: no parameter parsing of the SQL
                cursor.execute(run.sql)
                columns = [c.name for c in cursor.description or []]
                rows = cursor.fetchmany(MAX_ROWS + 1)
                run.status = QueryRunStatus.SUCCEEDED
                run.columns = columns
                run.truncated = len(rows) > MAX_ROWS or len(rows) > KEEP_ROWS
                run.row_count = min(len(rows), MAX_ROWS)
                run.rows = [[_json(v) for v in row] for row in rows[:KEEP_ROWS]]
            except DBAPIError as exc:
                run.status, run.error = QueryRunStatus.FAILED, str(exc.orig).splitlines()[0]
            except Exception as exc:  # noqa: BLE001 — psycopg errors raised by the raw cursor
                run.status, run.error = QueryRunStatus.FAILED, str(exc).splitlines()[0]
            finally:
                transaction.rollback()
        run.duration_ms = round((time.perf_counter() - started) * 1000)
