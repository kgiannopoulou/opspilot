from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.permissions import RoleName
from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.core.security import hash_password
from app.models import Role, User
from app.repositories.catalog import UserRepository
from app.schemas.auth import UserCreate, UserUpdate
from app.services import audit
from app.services.auth import AuthService


class UserAdminService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)

    def _role(self, name: str) -> Role:
        if name not in {r.value for r in RoleName}:
            raise ValidationFailed(f"unknown role {name!r}")
        return self.db.scalars(select(Role).where(Role.name == name)).one()

    def get(self, user_id: int) -> User:
        user = self.users.get(user_id)
        if user is None:
            raise NotFoundError(f"user {user_id} not found")
        return user

    def create(self, data: UserCreate, actor: User) -> User:
        if self.users.get_by_email(data.email):
            raise ConflictError("a user with this e-mail already exists")
        user = User(
            email=data.email.lower(),
            full_name=data.full_name,
            role_id=self._role(data.role).id,
            password_hash=hash_password(data.password),
        )
        self.db.add(user)
        self.db.flush()
        audit.record(self.db, actor, "user.created", "user", user.id, {"email": user.email, "role": data.role})
        self.db.commit()
        self.db.refresh(user)
        return user

    def update(self, user_id: int, data: UserUpdate, actor: User) -> User:
        user = self.get(user_id)
        changes: dict[str, object] = {}
        if data.full_name is not None:
            user.full_name = changes["full_name"] = data.full_name
        if data.role is not None and data.role != user.role.name:
            if user.id == actor.id:
                raise ValidationFailed("admins cannot change their own role")
            user.role_id = self._role(data.role).id
            changes["role"] = data.role
        if data.is_active is not None and data.is_active != user.is_active:
            if user.id == actor.id:
                raise ValidationFailed("admins cannot deactivate themselves")
            user.is_active = changes["is_active"] = data.is_active
            if not data.is_active:
                AuthService(self.db).revoke_all_for_user(user.id)
        if data.password is not None:
            user.password_hash = hash_password(data.password)
            AuthService(self.db).revoke_all_for_user(user.id)
            changes["password"] = "changed"
        audit.record(self.db, actor, "user.updated", "user", user.id, changes)
        self.db.commit()
        self.db.expire(user)
        return self.get(user_id)

    def delete(self, user_id: int, actor: User) -> None:
        """Soft delete: tickets and audit history keep pointing at the user, but the account can no longer log in."""
        user = self.get(user_id)
        if user.id == actor.id:
            raise ValidationFailed("admins cannot delete themselves")
        user.is_active = False
        user.password_hash = None
        AuthService(self.db).revoke_all_for_user(user.id)
        audit.record(self.db, actor, "user.deleted", "user", user.id, {"email": user.email})
        self.db.commit()
