from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, EmailStr, Field

from app.auth.permissions import permissions_for
from app.core.security import BCRYPT_MAX_BYTES
from app.models import User
from app.schemas.common import ORMModel


def _password_policy(value: str) -> str:
    if len(value.encode()) > BCRYPT_MAX_BYTES:
        raise ValueError(f"must be at most {BCRYPT_MAX_BYTES} bytes")
    if len(set(value)) < 4:
        raise ValueError("must not be a repeated character or a trivial pattern")
    return value


# Length over composition rules (NIST SP 800-63B): 12+ characters, any characters, within bcrypt's 72-byte input.
NewPassword = Annotated[str, Field(min_length=12, max_length=72), AfterValidator(_password_policy)]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class CurrentUser(ORMModel):
    id: int
    email: str
    full_name: str
    role: str
    permissions: list[str]
    last_login_at: datetime | None

    @classmethod
    def from_user(cls, user: User) -> "CurrentUser":
        return cls(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role.name,
            permissions=sorted(p.value for p in permissions_for(user.role.name)),
            last_login_at=user.last_login_at,
        )


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: CurrentUser


class UserAdminOut(ORMModel):
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "UserAdminOut":
        return cls(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role.name,
            is_active=user.is_active,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
        )


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    role: str
    password: NewPassword


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    role: str | None = None
    is_active: bool | None = None
    password: NewPassword | None = None
