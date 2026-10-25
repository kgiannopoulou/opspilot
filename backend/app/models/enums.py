from enum import StrEnum

from sqlalchemy import Enum


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    TRIAGED = "TRIAGED"
    INVESTIGATING = "INVESTIGATING"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class Priority(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class TicketSource(StrEnum):
    CUSTOMER_SUPPORT = "customer_support"
    MONITORING = "monitoring"
    EMAIL = "email"
    INTERNAL = "internal"


class TicketEventType(StrEnum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    STATUS_CHANGED = "STATUS_CHANGED"
    PRIORITY_CHANGED = "PRIORITY_CHANGED"
    ASSIGNED = "ASSIGNED"
    COMMENTED = "COMMENTED"
    LINKED_TO_INCIDENT = "LINKED_TO_INCIDENT"
    CATEGORY_CHANGED = "CATEGORY_CHANGED"
    AI_CLASSIFIED = "AI_CLASSIFIED"
    AI_SUGGESTION_REVIEWED = "AI_SUGGESTION_REVIEWED"


class TicketCategory(StrEnum):
    PAYMENTS = "PAYMENTS"
    AUTHENTICATION = "AUTHENTICATION"
    ORDERS = "ORDERS"
    NOTIFICATIONS = "NOTIFICATIONS"
    DATABASE = "DATABASE"
    PERFORMANCE = "PERFORMANCE"
    OTHER = "OTHER"


class ClassificationStatus(StrEnum):
    PENDING = "PENDING"  # waiting for a human
    ACCEPTED = "ACCEPTED"  # applied as suggested
    MODIFIED = "MODIFIED"  # applied with corrections
    REJECTED = "REJECTED"  # not applied
    SUPERSEDED = "SUPERSEDED"  # replaced by a newer suggestion before anyone reviewed it


class Severity(StrEnum):
    SEV1 = "SEV1"  # critical: core flow down for many customers
    SEV2 = "SEV2"  # major: degraded core flow or full outage of a secondary one
    SEV3 = "SEV3"
    SEV4 = "SEV4"


class IncidentStatus(StrEnum):
    INVESTIGATING = "INVESTIGATING"
    IDENTIFIED = "IDENTIFIED"
    MONITORING = "MONITORING"
    RESOLVED = "RESOLVED"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class DocumentCategory(StrEnum):
    RUNBOOK = "runbook"
    TROUBLESHOOTING = "troubleshooting"
    ARCHITECTURE = "architecture"
    PROCESS = "process"


class InvestigationStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EvidenceKind(StrEnum):
    LOG_PATTERN = "LOG_PATTERN"
    METRIC_DELTA = "METRIC_DELTA"
    DEPENDENCY_HEALTH = "DEPENDENCY_HEALTH"
    SIMILAR_INCIDENT = "SIMILAR_INCIDENT"
    DOCUMENT = "DOCUMENT"
    CHANGE_EVENT = "CHANGE_EVENT"


class EvidenceStance(StrEnum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    CONTEXT = "CONTEXT"


def db_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Store enums as VARCHAR + CHECK constraint: readable in psql, and adding a value is a simple migration."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )
