import ipaddress

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.rate_limit import LoginThrottle, get_login_throttle
from app.core.config import get_settings
from app.core.database import get_db
from app.models import User
from app.schemas.auth import CurrentUser, LoginRequest, TokenResponse
from app.services.auth import AuthError, AuthService, IssuedTokens

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "opspilot_refresh"


def client_ip(request: Request) -> str | None:
    """The peer address if it is a real IP (proxies/test clients may report a hostname)."""
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def _set_refresh_cookie(response: Response, tokens: IssuedTokens) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        max_age=settings.refresh_token_days * 24 * 3600,
        httponly=True,  # unreadable by JavaScript → not exfiltrable through XSS
        secure=settings.cookie_secure,
        samesite="strict",  # never sent on cross-site requests → refresh/logout are not CSRF-able
        path=f"{settings.api_prefix}/auth",  # only sent to the auth endpoints
    )


def _token_response(response: Response, tokens: IssuedTokens) -> TokenResponse:
    _set_refresh_cookie(response, tokens)
    response.headers["Cache-Control"] = "no-store"
    return TokenResponse(
        access_token=tokens.access_token, expires_in=tokens.expires_in, user=CurrentUser.from_user(tokens.user)
    )


@router.post("/login", response_model=TokenResponse)
def login(
    data: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    throttle: LoginThrottle = Depends(get_login_throttle),
) -> TokenResponse:
    ip = client_ip(request)
    throttle_key = ip or "unknown"
    if throttle.is_locked(data.email, throttle_key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many failed attempts, try again later")
    try:
        tokens = AuthService(db).login(data.email, data.password, ip, request.headers.get("user-agent"))
    except AuthError as exc:
        throttle.register_failure(data.email, throttle_key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid e-mail or password") from exc
    throttle.reset(data.email, throttle_key)
    return _token_response(response, tokens)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
) -> TokenResponse:
    """Rotate the refresh token (from the httpOnly cookie) and return a new access token."""
    if not refresh_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no refresh token")
    try:
        tokens = AuthService(db).refresh(refresh_token, request.headers.get("user-agent"))
    except AuthError as exc:
        response.delete_cookie(REFRESH_COOKIE, path=f"{get_settings().api_prefix}/auth")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired, please log in again") from exc
    return _token_response(response, tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    db: Session = Depends(get_db),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
) -> Response:
    AuthService(db).logout(refresh_token)
    response.status_code = status.HTTP_204_NO_CONTENT
    response.delete_cookie(REFRESH_COOKIE, path=f"{get_settings().api_prefix}/auth")
    return response


@router.get("/me", response_model=CurrentUser)
def me(user: User = Depends(get_current_user)) -> CurrentUser:
    return CurrentUser.from_user(user)
