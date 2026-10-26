"""👍 / 👎 on AI investigations, and the quality report built from it.

Feedback is only half the signal: analysts also accept, correct or reject triage suggestions (ticket_classifications)
and the grounding check rejects LLM drafts on its own. The report puts the three side by side.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ValidationFailed
from app.models import AIFeedback, AIInvestigation, Incident, User
from app.models.enums import FeedbackReason, InvestigationStatus
from app.services import audit
from app.services.classification import ClassificationService


class FeedbackService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _mine(self, run: AIInvestigation, user: User) -> AIFeedback | None:
        return self.db.scalar(
            select(AIFeedback).where(AIFeedback.investigation_id == run.id, AIFeedback.user_id == user.id)
        )

    def submit(
        self,
        incident: Incident,
        run: AIInvestigation,
        user: User,
        rating: int,
        reason: FeedbackReason | None = None,
        comment: str | None = None,
    ) -> AIFeedback:
        """Create or replace this user's verdict on a completed investigation."""
        if run.status != InvestigationStatus.COMPLETED:
            raise ConflictError(f"investigation {run.id} is {run.status.value}; only completed runs can be rated")
        if rating == -1 and reason is None:
            raise ValidationFailed("say why the investigation is incorrect (reason)")
        row = self._mine(run, user)
        if row is None:
            row = AIFeedback(investigation_id=run.id, user_id=user.id)
            self.db.add(row)
        row.rating, row.reason = rating, (reason if rating == -1 else None)
        row.comment = (comment or "").strip() or None
        audit.record(self.db, user, "ai.feedback_submitted", "incident", incident.number,
                     {"investigation_id": run.id, "rating": rating, "reason": row.reason and row.reason.value,
                      "engine": run.engine, "hypothesis": run.hypothesis_key})  # fmt: skip
        self.db.commit()
        self.db.refresh(row)
        return row

    def withdraw(self, incident: Incident, run: AIInvestigation, user: User) -> None:
        row = self._mine(run, user)
        if row is None:
            return
        self.db.delete(row)
        audit.record(self.db, user, "ai.feedback_withdrawn", "incident", incident.number, {"investigation_id": run.id})
        self.db.commit()

    def summary(self, run: AIInvestigation, user: User) -> dict[str, Any]:
        rows = list(self.db.scalars(select(AIFeedback).where(AIFeedback.investigation_id == run.id)).unique())
        return {
            "mine": next((r for r in rows if r.user_id == user.id), None),
            "useful": sum(r.rating == 1 for r in rows),
            "incorrect": sum(r.rating == -1 for r in rows),
            "reasons": dict(Counter(r.reason.value for r in rows if r.reason)),
        }

    def quality_report(self, recent: int = 10) -> dict[str, Any]:
        """Is the AI helping? Investigation feedback, triage review outcomes and grounding rejections in one place."""
        rows = list(
            self.db.execute(
                select(AIFeedback, AIInvestigation.engine, AIInvestigation.hypothesis_key, Incident.number)
                .join(AIInvestigation, AIInvestigation.id == AIFeedback.investigation_id)
                .join(Incident, Incident.id == AIInvestigation.incident_id)
                .order_by(AIFeedback.updated_at.desc())
            ).unique()
        )

        def bucket(key: str | None) -> dict[str, Any]:
            group = [r for r in rows if (r[1] if key == "engine" else r[2]) is not None]
            out: dict[str, Any] = {}
            for fb, engine, hypothesis, _ in group:
                name = str(engine if key == "engine" else hypothesis)
                entry = out.setdefault(name, {"useful": 0, "incorrect": 0})
                entry["useful" if fb.rating == 1 else "incorrect"] += 1
            for entry in out.values():
                entry["useful_rate"] = round(entry["useful"] / (entry["useful"] + entry["incorrect"]), 3)
            return out

        useful = sum(fb.rating == 1 for fb, *_ in rows)
        completed = self.db.execute(
            select(AIInvestigation.engine, AIInvestigation.grounding).where(
                AIInvestigation.status == InvestigationStatus.COMPLETED
            )
        ).all()
        # An LLM wrote it (engine "…/<model>"), or tried and was replaced by the extractive writer.
        llm_attempts = [
            g
            for engine, g in completed
            if not engine.endswith("extractive-v1") or {"rejected_llm_draft", "llm_error"} & g.keys()
        ]
        rejected = sum(1 for g in llm_attempts if "rejected_llm_draft" in g)
        statuses = dict(
            self.db.execute(select(AIInvestigation.status, func.count()).group_by(AIInvestigation.status)).all()
        )
        return {
            "investigations": {
                "completed": statuses.get(InvestigationStatus.COMPLETED, 0),
                "failed": statuses.get(InvestigationStatus.FAILED, 0),
                "rated": len({fb.investigation_id for fb, *_ in rows}),
                "votes": len(rows),
                "useful": useful,
                "incorrect": len(rows) - useful,
                "useful_rate": round(useful / len(rows), 3) if rows else None,
                "reasons": dict(Counter(fb.reason.value for fb, *_ in rows if fb.reason)),
                "by_engine": bucket("engine"),
                "by_hypothesis": bucket("hypothesis"),
                "grounding_passed": sum(1 for _, g in completed if g.get("passed")),
                "llm_drafts": len(llm_attempts),
                "llm_drafts_rejected": rejected,
            },
            "triage": ClassificationService(self.db).metrics(),
            "recent_negative": [
                {
                    "incident": number,
                    "investigation_id": fb.investigation_id,
                    "hypothesis": hypothesis,
                    "reason": fb.reason.value if fb.reason else None,
                    "comment": fb.comment,
                    "user": fb.user.full_name,
                    "at": fb.updated_at,
                }
                for fb, _engine, hypothesis, number in rows
                if fb.rating == -1
            ][:recent],
        }
