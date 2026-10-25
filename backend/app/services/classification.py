"""Human-in-the-loop triage: store AI suggestions, and apply them only when an analyst says so."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.classifier import Classifier, get_classifier
from app.ai.correlation import LogCorrelator
from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.models import Service, Ticket, TicketClassification, User
from app.models.enums import ClassificationStatus, Priority, TicketCategory, TicketEventType
from app.services.tickets import TicketService


class ClassificationService:
    def __init__(self, db: Session, classifier: Classifier | None = None) -> None:
        self.db = db
        self.classifier = classifier
        self.tickets = TicketService(db)

    def latest(self, ticket: Ticket) -> TicketClassification | None:
        return self.db.scalar(
            select(TicketClassification)
            .where(TicketClassification.ticket_id == ticket.id)
            .order_by(TicketClassification.created_at.desc(), TicketClassification.id.desc())
            .limit(1)
        )

    def classify(self, ticket: Ticket, actor: User | None = None) -> TicketClassification:
        """Predict and store a PENDING suggestion. The ticket itself is not changed."""
        classifier = self.classifier or get_classifier(self.db)
        correlation = LogCorrelator(self.db).correlate(ticket)
        prediction = classifier.classify(ticket.title, ticket.description, correlation)
        service = None
        if prediction.service:
            service = self.db.scalar(select(Service).where(Service.name == prediction.service))
        # A newer suggestion supersedes any earlier one still waiting for review.
        for stale in self.db.scalars(
            select(TicketClassification).where(
                TicketClassification.ticket_id == ticket.id,
                TicketClassification.status == ClassificationStatus.PENDING,
            )
        ):
            stale.status = ClassificationStatus.SUPERSEDED
        row = TicketClassification(
            ticket_id=ticket.id,
            engine=prediction.engine,
            category=prediction.category,
            priority=prediction.priority,
            service_id=service.id if service else None,
            confidence=prediction.confidence,
            rationale=prediction.rationale,
            signals=prediction.signals,
        )
        self.db.add(row)
        self.db.flush()
        self._ticket_event(
            ticket,
            actor,
            TicketEventType.AI_CLASSIFIED,
            to_value=f"{prediction.category.value} · {prediction.priority.value}",
            data={"classification_id": row.id, "engine": prediction.engine, "confidence": prediction.confidence},
        )
        self.db.commit()
        self.db.refresh(row)
        return row

    def review(
        self,
        ticket: Ticket,
        classification_id: int,
        action: str,
        actor: User,
        category: TicketCategory | None = None,
        priority: Priority | None = None,
        service: str | None = None,
        note: str | None = None,
    ) -> TicketClassification:
        row = self.db.scalar(
            select(TicketClassification)
            .where(TicketClassification.id == classification_id, TicketClassification.ticket_id == ticket.id)
            .with_for_update(of=TicketClassification)
        )
        if row is None:
            raise NotFoundError(f"classification {classification_id} not found on {ticket.number}")
        if row.status != ClassificationStatus.PENDING:
            raise ConflictError(f"classification {classification_id} was already reviewed ({row.status.value})")

        row.reviewed_by_id, row.reviewed_at, row.review_note = actor.id, datetime.now(UTC), note
        if action == "reject":
            row.status = ClassificationStatus.REJECTED
            self._ticket_event(ticket, actor, TicketEventType.AI_SUGGESTION_REVIEWED, to_value="REJECTED",
                               data={"classification_id": row.id, "note": note})  # fmt: skip
            self.db.commit()
            return row

        final_service = row.service
        if action == "modify" and service is not None:
            final_service = self.db.scalar(select(Service).where(Service.name == service))
            if final_service is None:
                raise ValidationFailed(f"unknown service {service!r}")
        row.final_category = (category if action == "modify" and category else None) or row.category
        row.final_priority = (priority if action == "modify" and priority else None) or row.priority
        row.final_service_id = final_service.id if final_service else None
        corrected = [
            name
            for name, predicted, final in (
                ("category", row.category, row.final_category),
                ("priority", row.priority, row.final_priority),
                ("service", row.service_id, row.final_service_id),
            )
            if predicted != final
        ]
        row.status = ClassificationStatus.MODIFIED if corrected else ClassificationStatus.ACCEPTED
        changes = self._apply(ticket, actor, row.final_category, row.final_priority, final_service)
        self._ticket_event(
            ticket,
            actor,
            TicketEventType.AI_SUGGESTION_REVIEWED,
            to_value=row.status.value,
            data={"classification_id": row.id, "corrected": corrected, "applied": changes, "note": note},
        )
        self.db.commit()
        self.db.refresh(row)
        return row

    def _apply(
        self, ticket: Ticket, actor: User, category: TicketCategory, priority: Priority, service: Service | None
    ) -> list[str]:
        applied = []
        if ticket.category != category:
            previous = ticket.category.value if ticket.category else None
            self._ticket_event(
                ticket, actor, TicketEventType.CATEGORY_CHANGED, from_value=previous, to_value=category.value
            )
            ticket.category = category
            applied.append("category")
        if ticket.priority != priority:
            self._ticket_event(ticket, actor, TicketEventType.PRIORITY_CHANGED,
                               from_value=ticket.priority.value, to_value=priority.value)  # fmt: skip
            ticket.priority = priority
            applied.append("priority")
        if service is not None and ticket.service_id != service.id:
            ticket.service_id = service.id
            self._ticket_event(ticket, actor, TicketEventType.UPDATED, data={"fields": ["service"]})
            applied.append("service")
        return applied

    def _ticket_event(self, ticket: Ticket, actor: User | None, type_: TicketEventType, **kwargs: Any) -> None:
        """actor=None means the system (e.g. the background worker) did it."""
        self.tickets._event(ticket, actor, type_, **kwargs)  # timeline + audit, same transaction

    def metrics(self) -> dict[str, Any]:
        """How often analysts agree with the AI, and what they correct: the feedback loop for the classifier."""
        counts = dict(
            self.db.execute(
                select(TicketClassification.status, func.count()).group_by(TicketClassification.status)
            ).all()
        )
        reviewed = [
            row
            for row in self.db.scalars(
                select(TicketClassification).where(
                    TicketClassification.status.in_([ClassificationStatus.ACCEPTED, ClassificationStatus.MODIFIED])
                )
            ).unique()
        ]
        corrections = {
            "category": sum(r.final_category != r.category for r in reviewed),
            "priority": sum(r.final_priority != r.priority for r in reviewed),
            "service": sum(r.final_service_id != r.service_id for r in reviewed),
        }
        decided = len(reviewed) + counts.get(ClassificationStatus.REJECTED, 0)
        accepted = counts.get(ClassificationStatus.ACCEPTED, 0)
        return {
            "total": sum(counts.values()),
            "by_status": {s.value: counts.get(s, 0) for s in ClassificationStatus},
            "acceptance_rate": round(accepted / decided, 3) if decided else None,
            "corrections": corrections,
        }
