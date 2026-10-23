from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.permissions import Permission, has_permission
from app.core.database import get_db
from app.core.security import InvalidTokenError, decode_access_token
from app.models import User

bearer = HTTPBearer(auto_error=False, description="Access token from POST /auth/login or /auth/refresh")

UNAUTHENTICATED = HTTPException(
    status.HTTP_401_UNAUTHORIZED, "not authenticated", headers={"WWW-Authenticate": "Bearer"}
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise UNAUTHENTICATED
    try:
        payload = decode_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise UNAUTHENTICATED from exc
    user = db.get(User, int(payload["sub"]))
    # Re-check the database on every request: deactivation takes effect immediately, not when the JWT expires.
    if user is None or not user.is_active:
        raise UNAUTHENTICATED
    return user


def require(permission: Permission) -> Callable[..., User]:
    """Dependency factory: `user: User = Depends(require(Permission.USERS_MANAGE))`."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user.role.name, permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission {permission.value}")
        return user

    return checker
