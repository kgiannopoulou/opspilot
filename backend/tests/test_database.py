"""The schema itself enforces the important invariants — these tests prove it, independent of the service layer."""

from datetime import UTC, datetime, timedelta

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError, ValidationFailed
from app.models import (
    AIInvestigation,
    AuditEvent,
    Base,
    DocumentChunk,
    Incident,
    IncidentTicket,
    KnowledgeDocument,
    MetricSample,
    Role,
    Service,
    service_dependencies,
)
from app.models.enums import DocumentCategory, Severity
from app.schemas.ticket import TicketCreate
from app.services.incidents import IncidentService
from app.services.tickets import TicketService

NOW = datetime(2026, 10, 22, 14, 27, tzinfo=UTC)


def test_models_match_migrations(engine):
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_roles_are_reference_data(db):
    assert sorted(db.scalars(select(Role.name))) == ["admin", "engineer", "support_agent", "viewer"]


def test_ticket_can_belong_to_only_one_incident(db, seeded):
    ticket = TicketService(db).create(TicketCreate(title="Checkout broken", description="x"), seeded["agent"])
    service = IncidentService(db)
    first = service.declare(
        title="A", severity=Severity.SEV1, primary_service="payment-service", started_at=NOW, actor=None,
        ticket_numbers=[ticket.number],
    )  # fmt: skip
    second = service.declare(title="B", severity=Severity.SEV2, primary_service="payment-service", started_at=NOW,
                              actor=None)  # fmt: skip
    with pytest.raises(ConflictError):
        service.link_ticket(second, ticket.number, None)
    db.rollback()

    # Even bypassing the service, the unique constraint holds.
    db.add(IncidentTicket(incident_id=second.id, ticket_id=ticket.id))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
    assert db.scalar(select(func.count()).select_from(IncidentTicket)) == 1
    assert first.number == "INC-101"


def test_declare_is_atomic(db, seeded):
    with pytest.raises(ValidationFailed):
        IncidentService(db).declare(
            title="Broken", severity=Severity.SEV2, primary_service="payment-service", started_at=NOW,
            actor=None, ticket_numbers=["TCK-2026-99999"],
        )  # fmt: skip
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Incident)) == 0
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_service_cannot_depend_on_itself(db, seeded):
    payment = seeded["payment"]
    with pytest.raises(IntegrityError, match="no_self_dependency"):
        db.execute(service_dependencies.insert().values(service_id=payment.id, depends_on_id=payment.id))


def test_metric_series_is_unique_per_timestamp(db, seeded):
    sample = dict(ts=NOW, service_id=seeded["payment"].id, name="error_rate", value=0.1, unit="ratio")
    db.add(MetricSample(**sample))
    db.flush()
    db.add(MetricSample(**sample))
    with pytest.raises(IntegrityError, match="uq_metric_samples_series_ts"):
        db.flush()


def test_investigation_confidence_must_be_a_probability(db, seeded):
    incident = IncidentService(db).declare(
        title="A", severity=Severity.SEV1, primary_service="payment-service", started_at=NOW, actor=None
    )
    db.add(
        AIInvestigation(
            incident_id=incident.id, engine="rules-v1", confidence=1.7, window_start=NOW - timedelta(minutes=30),
            window_end=NOW,
        )
    )  # fmt: skip
    with pytest.raises(IntegrityError, match="confidence_range"):
        db.flush()


def test_ticket_changes_are_audited_in_the_same_transaction(db, seeded):
    ticket = TicketService(db).create(TicketCreate(title="Checkout broken", description="x"), seeded["agent"])
    audit = db.scalars(select(AuditEvent).where(AuditEvent.entity_id == ticket.number)).all()
    assert [a.action for a in audit] == ["ticket.created"]
    assert audit[0].actor_id == seeded["agent"].id


def test_pgvector_similarity_search(db, seeded):
    doc = KnowledgeDocument(
        slug="gateway-timeout", title="Gateway timeout runbook", category=DocumentCategory.RUNBOOK,
        content="...", content_hash="0" * 64,
    )  # fmt: skip
    db.add(doc)
    db.flush()
    near, far = [1.0] + [0.0] * 383, [0.0] * 383 + [1.0]
    db.add_all(
        [
            DocumentChunk(document_id=doc.id, chunk_index=0, content="near", token_count=1, embedding=near),
            DocumentChunk(document_id=doc.id, chunk_index=1, content="far", token_count=1, embedding=far),
        ]
    )
    db.flush()
    query = [0.9, 0.1] + [0.0] * 382
    best = db.scalar(select(DocumentChunk.content).order_by(DocumentChunk.embedding.cosine_distance(query)).limit(1))
    assert best == "near"
    assert db.scalar(text("SELECT extname FROM pg_extension WHERE extname = 'vector'")) == "vector"


def test_service_dependency_graph(db, seeded):
    payment, auth = seeded["payment"], seeded["auth"]
    payment.dependencies = [auth]
    db.commit()
    db.expire_all()
    assert [s.name for s in db.get(Service, payment.id).dependencies] == ["auth-service"]
