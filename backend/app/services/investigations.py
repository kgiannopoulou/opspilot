"""Investigations as an asynchronous pipeline.

    POST /investigations ─► request(): QUEUED row + job ─► 202 Accepted (the API never waits for an LLM)
    worker ─► execute(): signals → hypotheses → documents → write-up → queries → save, reporting each stage

`run()` does both in-process (seed data, tests, and the fallback when Redis is unavailable).
"""

import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.engine import ENGINE_VERSION, InvestigationEngine
from app.ai.llm import get_llm
from app.ai.query_planner import QueryPlanner
from app.ai.writer import Writer, get_writer
from app.core.errors import ConflictError, NotFoundError
from app.models import AIEvidence, AIInvestigation, Incident, InvestigationQuery, User
from app.models.enums import InvestigationStatus, QueryRisk
from app.services import audit

# (key, label) in execution order; the engine reports the first three itself (see InvestigationEngine.investigate).
PIPELINE: list[tuple[str, str]] = [
    ("signals", "Search logs & metrics"),
    ("hypotheses", "Correlate changes & history, score hypotheses"),
    ("documents", "Retrieve runbooks (pgvector)"),
    ("write", "Write cited summary & check grounding"),
    ("queries", "Propose investigation queries"),
    ("save", "Save result"),
]
ACTIVE = (InvestigationStatus.QUEUED, InvestigationStatus.RUNNING)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def initial_progress() -> list[dict[str, Any]]:
    return [{"key": k, "label": label, "status": "pending", "started_at": None, "finished_at": None}
            for k, label in PIPELINE]  # fmt: skip


class InvestigationService:
    def __init__(self, db: Session, writer: Writer | None = None, planner: QueryPlanner | None = None) -> None:
        self.db = db
        self.writer = writer
        self.planner = planner

    # -- reads ---------------------------------------------------------------------------------------------------

    def latest(self, incident: Incident) -> AIInvestigation | None:
        return self.db.scalar(
            select(AIInvestigation)
            .where(AIInvestigation.incident_id == incident.id)
            .order_by(AIInvestigation.created_at.desc(), AIInvestigation.id.desc())
            .limit(1)
        )

    def history(self, incident: Incident) -> list[AIInvestigation]:
        return list(
            self.db.scalars(
                select(AIInvestigation)
                .where(AIInvestigation.incident_id == incident.id)
                .order_by(AIInvestigation.created_at.desc(), AIInvestigation.id.desc())
            ).unique()
        )

    def get(self, incident: Incident, investigation_id: int) -> AIInvestigation:
        run = self.db.get(AIInvestigation, investigation_id)
        if run is None or run.incident_id != incident.id:
            raise NotFoundError(f"investigation {investigation_id} not found on {incident.number}")
        return run

    # -- pipeline ------------------------------------------------------------------------------------------------

    def request(self, incident: Incident, actor: User | None, now: datetime | None = None) -> AIInvestigation:
        """Create a QUEUED investigation. One active investigation per incident: a second request is a conflict."""
        active = self.db.scalar(
            select(AIInvestigation.id).where(
                AIInvestigation.incident_id == incident.id, AIInvestigation.status.in_(ACTIVE)
            )
        )
        if active is not None:
            raise ConflictError(f"investigation {active} is already in progress for {incident.number}")
        run = AIInvestigation(
            incident_id=incident.id,
            requested_by_id=actor.id if actor else None,
            status=InvestigationStatus.QUEUED,
            engine=ENGINE_VERSION,
            window_start=incident.started_at,
            window_end=incident.resolved_at or now or datetime.now(UTC),
            progress=initial_progress(),
        )
        self.db.add(run)
        self.db.flush()
        audit.record(self.db, actor, "incident.investigation_requested", "incident", incident.number,
                     {"investigation_id": run.id})  # fmt: skip
        self.db.commit()
        return run

    def _step(self, run: AIInvestigation, key: str) -> None:
        """Finish the running stage, start `key`, and commit so pollers see it immediately."""
        now = _now()
        progress = [dict(s) for s in run.progress]
        for stage in progress:
            if stage["status"] == "running":
                stage.update(status="done", finished_at=now)
            if stage["key"] == key:
                stage.update(status="running", started_at=now)
        run.progress = progress
        self.db.commit()

    def execute(self, run: AIInvestigation, final_attempt: bool = True) -> AIInvestigation:
        """Run the pipeline for a QUEUED (or retried) investigation. Completed runs are left alone (idempotent)."""
        if run.status == InvestigationStatus.COMPLETED:
            return run
        incident = self.db.get(Incident, run.incident_id)
        started = time.perf_counter()
        run.status, run.started_at, run.error = InvestigationStatus.RUNNING, datetime.now(UTC), None
        run.attempts = (run.attempts or 0) + 1
        run.progress = initial_progress()
        audit.record(self.db, None, "incident.investigation_started", "incident", incident.number,
                     {"investigation_id": run.id, "attempt": run.attempts})  # fmt: skip
        self.db.commit()
        writer = self.writer or get_writer()
        try:
            result = InvestigationEngine(self.db).investigate(incident, step=lambda key: self._step(run, key))
            self._step(run, "write")
            draft = writer.write(incident, result)
            self._step(run, "queries")
            proposals = (self.planner or QueryPlanner(get_llm())).plan(incident, result)
            self._step(run, "save")
        except Exception as exc:
            self.db.rollback()
            self._fail(run, exc, started, final_attempt)
            raise

        run.status = InvestigationStatus.COMPLETED
        run.engine = f"{ENGINE_VERSION}+rag/{draft.writer}"[:64]
        run.hypothesis_key = result.hypothesis_key
        run.root_cause = draft.root_cause
        run.narrative = draft.narrative
        run.search_query = result.search_query
        run.confidence = result.confidence
        run.recommended_steps = draft.recommended_steps
        run.grounding = draft.grounding
        run.suggested_sql = result.suggested_sql
        run.window_start, run.window_end = result.window_start, result.window_end
        run.evidence = [
            AIEvidence(
                position=i,
                kind=e.kind,
                stance=e.stance,
                summary=e.summary,
                weight=e.weight,
                data=e.data,
                related_incident_id=e.related_incident_id,
                document_id=e.document_id,
                ref=e.ref,
                chunk_id=e.chunk_id,
            )
            for i, e in enumerate(result.evidence)
        ]
        run.queries = [
            InvestigationQuery(
                position=i,
                title=p.title,
                purpose=p.purpose,
                sql=p.sql,
                safe_sql=p.verdict.safe_sql,
                source=p.source[:64],
                risk=QueryRisk.READ_ONLY if p.verdict.ok else QueryRisk.BLOCKED,
                violations=p.verdict.violations,
                tables=p.verdict.tables,
            )
            for i, p in enumerate(proposals)
        ]
        now = _now()
        run.progress = [{**s, "status": "done", "finished_at": s["finished_at"] or now} for s in run.progress]
        run.duration_ms = round((time.perf_counter() - started) * 1000)
        run.completed_at = datetime.now(UTC)
        audit.record(
            self.db,
            run.requested_by,
            "incident.investigated",
            "incident",
            incident.number,
            {"investigation_id": run.id, "hypothesis": result.hypothesis_key, "confidence": result.confidence},
        )
        self.db.commit()
        return run

    def _fail(self, run: AIInvestigation, exc: Exception, started: float, final_attempt: bool) -> None:
        """Record the failure on the run. Not final: back to QUEUED for the worker's retry, error kept for the UI."""
        self.db.refresh(run)
        now = _now()
        progress = [dict(s) for s in run.progress]
        # The stage that was running failed; if none had started yet, the first unfinished one did.
        failed = next((s for s in progress if s["status"] == "running"), None) or next(
            (s for s in progress if s["status"] != "done"), None
        )
        if failed is not None:
            failed.update(status="failed", finished_at=now)
        run.progress = progress
        run.error = f"{type(exc).__name__}: {exc}"
        run.duration_ms = round((time.perf_counter() - started) * 1000)
        if final_attempt:
            run.status, run.completed_at = InvestigationStatus.FAILED, datetime.now(UTC)
            incident = self.db.get(Incident, run.incident_id)
            audit.record(self.db, None, "incident.investigation_failed", "incident", incident.number,
                         {"investigation_id": run.id, "error": type(exc).__name__})  # fmt: skip
        else:
            run.status = InvestigationStatus.QUEUED
        self.db.commit()

    def run(self, incident: Incident, actor: User | None, now: datetime | None = None) -> AIInvestigation:
        """Request and execute in-process (seed data, tests, fallback). A failure is stored, then re-raised."""
        return self.execute(self.request(incident, actor, now))
