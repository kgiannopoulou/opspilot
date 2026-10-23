from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.core.database import get_db
from app.models import User
from app.repositories.catalog import UserRepository
from app.schemas.auth import UserAdminOut, UserCreate, UserUpdate
from app.schemas.common import UserRef
from app.services.users import UserAdminService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserRef])
def list_users(db: Session = Depends(get_db), _: User = Depends(require(Permission.USERS_READ))) -> list[UserRef]:
    """Active users — used by the UI's assignee picker."""
    return [UserRef.model_validate(u) for u in UserRepository(db).list()]


@router.get("/admin", response_model=list[UserAdminOut])
def list_users_admin(
    db: Session = Depends(get_db), _: User = Depends(require(Permission.USERS_MANAGE))
) -> list[UserAdminOut]:
    return [UserAdminOut.from_user(u) for u in UserRepository(db).list(active_only=False)]


@router.post("", response_model=UserAdminOut, status_code=status.HTTP_201_CREATED)
def create_user(
    data: UserCreate, db: Session = Depends(get_db), admin: User = Depends(require(Permission.USERS_MANAGE))
) -> UserAdminOut:
    return UserAdminOut.from_user(UserAdminService(db).create(data, admin))


@router.patch("/{user_id}", response_model=UserAdminOut)
def update_user(
    user_id: int,
    data: UserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require(Permission.USERS_MANAGE)),
) -> UserAdminOut:
    return UserAdminOut.from_user(UserAdminService(db).update(user_id, data, admin))


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int, db: Session = Depends(get_db), admin: User = Depends(require(Permission.USERS_MANAGE))
) -> Response:
    UserAdminService(db).delete(user_id, admin)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
