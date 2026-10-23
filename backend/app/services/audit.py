from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent, User


def record(
    db: Session,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: str | int,
    changes: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    """Add an audit row to the caller's transaction — it commits or rolls back together with the change itself."""
    db.add(
        AuditEvent(
            actor_id=actor.id if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            changes=changes or {},
            ip_address=ip_address,
        )
    )
