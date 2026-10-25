"""Importing this package registers every model on Base.metadata (needed by Alembic)."""

from app.models.audit import AuditEvent
from app.models.base import Base
from app.models.classification import TicketClassification
from app.models.incident import Incident, IncidentTicket, incident_services
from app.models.investigation import AIEvidence, AIInvestigation
from app.models.knowledge import DocumentChunk, KnowledgeDocument
from app.models.observability import ApplicationLog, MetricSample
from app.models.service import Service, service_dependencies
from app.models.ticket import Ticket, TicketComment, TicketEvent
from app.models.user import RefreshToken, Role, User

__all__ = [
    "AIEvidence",
    "AIInvestigation",
    "ApplicationLog",
    "AuditEvent",
    "Base",
    "DocumentChunk",
    "Incident",
    "IncidentTicket",
    "KnowledgeDocument",
    "MetricSample",
    "RefreshToken",
    "Role",
    "Service",
    "Ticket",
    "TicketClassification",
    "TicketComment",
    "TicketEvent",
    "User",
    "incident_services",
    "service_dependencies",
]
