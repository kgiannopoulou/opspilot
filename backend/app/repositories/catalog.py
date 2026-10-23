from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Service, User


class ServiceRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_name(self, name: str) -> Service | None:
        return self.db.scalar(select(Service).where(Service.name == name))

    def list(self) -> Sequence[Service]:
        return self.db.scalars(select(Service).order_by(Service.tier, Service.name)).all()


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, user_id: int) -> User | None:
        return self.db.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        return self.db.scalar(select(User).where(User.email == email.lower()))

    def list(self, active_only: bool = True) -> Sequence[User]:
        stmt = select(User).order_by(User.full_name)
        if active_only:
            stmt = stmt.where(User.is_active.is_(True))
        return self.db.scalars(stmt).all()
