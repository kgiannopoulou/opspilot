"""Importing this package registers every model on Base.metadata (needed by Alembic)."""

from app.models.base import Base
from app.models.service import Service
from app.models.ticket import Ticket, TicketComment, TicketEvent
from app.models.user import User

__all__ = ["Base", "Service", "Ticket", "TicketComment", "TicketEvent", "User"]
