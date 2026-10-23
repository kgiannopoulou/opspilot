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
