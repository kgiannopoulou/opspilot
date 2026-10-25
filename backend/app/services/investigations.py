import time
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.engine import ENGINE_VERSION, InvestigationEngine
from app.ai.writer import Writer, get_writer
from app.models import AIEvidence, AIInvestigation, Incident, User
from app.models.enums import InvestigationStatus
from app.services import audit


class InvestigationService:
    def __init__(self, db: Session, writer: Writer | None = None) -> None:
        self.db = db
        self.writer = writer

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

    def run(self, incident: Incident, actor: User | None, now: datetime | None = None) -> AIInvestigation:
        """Run the engine and store the run with all its evidence in one transaction.

        A failing engine is recorded as a FAILED run (with the error) rather than lost, so the UI can show it.
        """
        started = time.perf_counter()
        run = AIInvestigation(
            incident_id=incident.id,
            requested_by_id=actor.id if actor else None,
            engine=ENGINE_VERSION,
            window_start=incident.started_at,
            window_end=incident.resolved_at or now or datetime.now(UTC),
        )
        writer = self.writer or get_writer()
        try:
            result = InvestigationEngine(self.db).investigate(incident)
            draft = writer.write(incident, result)
        except Exception as exc:  # noqa: BLE001 — persisted for the analyst, then re-raised below
            self.db.rollback()
            run.status = InvestigationStatus.FAILED
            run.error = f"{type(exc).__name__}: {exc}"
            run.duration_ms = round((time.perf_counter() - started) * 1000)
            run.completed_at = datetime.now(UTC)
            self.db.add(run)
            self.db.commit()
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
        run.duration_ms = round((time.perf_counter() - started) * 1000)
        run.completed_at = datetime.now(UTC)
        self.db.add(run)
        self.db.flush()
        audit.record(
            self.db,
            actor,
            "incident.investigated",
            "incident",
            incident.number,
            {"investigation_id": run.id, "hypothesis": result.hypothesis_key, "confidence": result.confidence},
        )
        self.db.commit()
        return run
