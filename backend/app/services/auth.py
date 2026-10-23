import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.models import RefreshToken, User
from app.repositories.catalog import UserRepository
from app.services import audit


class AuthError(Exception):
    """Deliberately generic: callers must not learn whether the e-mail, password or token was wrong."""


@dataclass
class IssuedTokens:
    user: User
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    def _issue(self, user: User, family_id: uuid.UUID, user_agent: str | None) -> tuple[IssuedTokens, RefreshToken]:
        access, expires_in = create_access_token(user.id, user.role.name)
        raw = new_refresh_token()
        expires_at = datetime.now(UTC) + timedelta(days=self.settings.refresh_token_days)
        record = RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(raw),
            family_id=family_id,
            expires_at=expires_at,
            user_agent=(user_agent or "")[:255] or None,
        )
        self.db.add(record)
        self.db.flush()
        return IssuedTokens(user, access, expires_in, raw, expires_at), record

    def login(self, email: str, password: str, ip: str | None, user_agent: str | None) -> IssuedTokens:
        user = UserRepository(self.db).get_by_email(email)
        # verify_password runs bcrypt even for unknown users, keeping response times uniform.
        if not verify_password(password, user.password_hash if user else None) or user is None or not user.is_active:
            audit.record(self.db, user, "auth.login_failed", "user", email.lower(), ip_address=ip)
            self.db.commit()
            raise AuthError("invalid credentials")
        user.last_login_at = datetime.now(UTC)
        tokens, _ = self._issue(user, uuid.uuid4(), user_agent)
        audit.record(self.db, user, "auth.login_succeeded", "user", user.id, ip_address=ip)
        self.db.commit()
        return tokens

    def refresh(self, raw_token: str, user_agent: str | None) -> IssuedTokens:
        now = datetime.now(UTC)
        record = self.db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token)).with_for_update()
        )
        if record is None:
            raise AuthError("invalid refresh token")
        if record.revoked_at is not None:
            # Reuse of a rotated token: someone holds a stolen copy. Kill every session from that login.
            self.revoke_family(record.family_id)
            audit.record(self.db, record.user, "auth.refresh_reuse_detected", "user", record.user_id)
            self.db.commit()
            raise AuthError("refresh token reuse detected")
        if record.expires_at <= now or not record.user.is_active:
            raise AuthError("refresh token expired")

        tokens, replacement = self._issue(record.user, record.family_id, user_agent)
        record.revoked_at = now
        record.replaced_by_id = replacement.id
        self.db.commit()
        return tokens

    def logout(self, raw_token: str | None) -> None:
        if not raw_token:
            return
        record = self.db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token)))
        if record is not None:
            self.revoke_family(record.family_id)
            audit.record(self.db, record.user, "auth.logout", "user", record.user_id)
            self.db.commit()

    def revoke_family(self, family_id: uuid.UUID) -> None:
        self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )

    def revoke_all_for_user(self, user_id: int) -> None:
        self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
