from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import User
from app.repositories.catalog import UserRepository


def get_current_user(
    x_user_email: str | None = Header(default=None, description="Temporary actor header until JWT auth lands"),
    db: Session = Depends(get_db),
) -> User:
    """Who is performing the request.

    Placeholder: the acting user is taken from the `X-User-Email` header so the ticket workflow can be exercised
    before authentication exists. Replaced by JWT bearer authentication in the auth step.
    """
    user = UserRepository(db).get_by_email(x_user_email) if x_user_email else None
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown or inactive user")
    return user
