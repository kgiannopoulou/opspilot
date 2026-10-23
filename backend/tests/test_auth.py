from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import select

from app.auth.permissions import Permission, RoleName, has_permission, permissions_for
from app.core.config import Settings, get_settings
from app.core.security import ALGORITHM, create_access_token, decode_access_token
from app.models import AuditEvent, RefreshToken
from tests.conftest import PASSWORD, as_user

AUTH = "/api/v1/auth"


def login(client, email, password=PASSWORD):
    return client.post(f"{AUTH}/login", json={"email": email, "password": password})


# --- login / refresh / logout --------------------------------------------------------------------------------


def test_login_returns_access_token_and_httponly_refresh_cookie(client, seeded):
    response = login(client, "maria@opspilot.dev")
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 15 * 60
    assert body["user"]["role"] == "engineer"
    assert "incidents:investigate" in body["user"]["permissions"]
    assert "refresh_token" not in body  # only ever in the httpOnly cookie

    cookie = response.headers["set-cookie"]
    assert "opspilot_refresh=" in cookie
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api/v1/auth" in cookie
    assert response.headers["cache-control"] == "no-store"

    me = client.get(f"{AUTH}/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["email"] == "maria@opspilot.dev"


def test_login_failures_are_generic(client, seeded):
    wrong_password = login(client, "maria@opspilot.dev", "nope")
    unknown_user = login(client, "ghost@opspilot.dev", "nope")
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


def test_login_is_audited(client, seeded, db):
    login(client, "maria@opspilot.dev", "nope")
    login(client, "maria@opspilot.dev")
    actions = db.scalars(select(AuditEvent.action).order_by(AuditEvent.id)).all()
    assert actions == ["auth.login_failed", "auth.login_succeeded"]


def test_login_is_throttled_after_repeated_failures(client, seeded):
    for _ in range(5):
        assert login(client, "maria@opspilot.dev", "wrong").status_code == 401
    # Even the correct password is refused while locked out.
    assert login(client, "maria@opspilot.dev").status_code == 429


def test_inactive_user_cannot_log_in(client, seeded, db):
    seeded["maria"].is_active = False
    db.commit()
    assert login(client, "maria@opspilot.dev").status_code == 401


def test_refresh_rotates_the_token(client, seeded, db):
    login(client, "maria@opspilot.dev")
    first_cookie = client.cookies.get("opspilot_refresh")
    refreshed = client.post(f"{AUTH}/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]
    assert client.cookies.get("opspilot_refresh") != first_cookie

    tokens = db.scalars(select(RefreshToken).order_by(RefreshToken.id)).all()
    assert len(tokens) == 2
    assert tokens[0].revoked_at is not None and tokens[0].replaced_by_id == tokens[1].id
    assert tokens[0].family_id == tokens[1].family_id


def test_reusing_a_rotated_refresh_token_revokes_the_whole_family(client, seeded, db):
    login(client, "maria@opspilot.dev")
    stolen = client.cookies.get("opspilot_refresh")
    assert client.post(f"{AUTH}/refresh").status_code == 200  # legitimate rotation

    client.cookies.set("opspilot_refresh", stolen, path="/api/v1/auth")
    assert client.post(f"{AUTH}/refresh").status_code == 401  # replay detected

    db.expire_all()
    assert all(t.revoked_at is not None for t in db.scalars(select(RefreshToken)))
    assert "auth.refresh_reuse_detected" in db.scalars(select(AuditEvent.action)).all()


def test_logout_revokes_refresh_token(client, seeded):
    login(client, "maria@opspilot.dev")
    cookie = client.cookies.get("opspilot_refresh")
    assert client.post(f"{AUTH}/logout").status_code == 204
    client.cookies.set("opspilot_refresh", cookie, path="/api/v1/auth")
    assert client.post(f"{AUTH}/refresh").status_code == 401


def test_refresh_without_cookie(client, seeded):
    assert client.post(f"{AUTH}/refresh").status_code == 401


# --- access tokens -------------------------------------------------------------------------------------------


def test_expired_access_token_rejected(client, seeded):
    token, _ = create_access_token(seeded["maria"].id, "engineer", now=datetime.now(UTC) - timedelta(hours=1))
    assert client.get(f"{AUTH}/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_tampered_and_foreign_tokens_rejected(client, seeded):
    settings = get_settings()
    claims = {"sub": str(seeded["maria"].id), "typ": "access", "iss": settings.jwt_issuer, "iat": datetime.now(UTC),
              "exp": datetime.now(UTC) + timedelta(minutes=5)}  # fmt: skip
    forged = jwt.encode(claims, "attacker-secret-attacker-secret-1234", algorithm=ALGORITHM)
    unsigned = jwt.encode(claims, None, algorithm="none")
    for token in (forged, unsigned):
        assert client.get(f"{AUTH}/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_deactivation_takes_effect_before_token_expiry(client, seeded, db):
    headers = as_user(seeded["maria"])
    assert client.get(f"{AUTH}/me", headers=headers).status_code == 200
    seeded["maria"].is_active = False
    db.commit()
    assert client.get(f"{AUTH}/me", headers=headers).status_code == 401


def test_token_type_is_checked():
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {"sub": "1", "typ": "refresh", "iss": settings.jwt_issuer, "iat": now, "exp": now + timedelta(minutes=5)}
    token = jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)
    with pytest.raises(Exception, match="not an access token"):
        decode_access_token(token)


def test_production_requires_a_real_secret():
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(environment="production")
    assert Settings(environment="production", jwt_secret="x" * 40).environment == "production"


# --- role-based access control --------------------------------------------------------------------------------


def test_permission_matrix():
    assert has_permission("viewer", Permission.TICKETS_READ)
    assert not has_permission("viewer", Permission.TICKETS_WRITE)
    assert has_permission("support_agent", Permission.TICKETS_WRITE)
    assert not has_permission("support_agent", Permission.INCIDENTS_INVESTIGATE)
    assert has_permission("engineer", Permission.INCIDENTS_INVESTIGATE)
    assert not has_permission("engineer", Permission.USERS_MANAGE)
    assert permissions_for(RoleName.ADMIN) == frozenset(Permission)
    assert permissions_for("hacker") == frozenset()


def test_viewer_is_read_only(client, seeded):
    headers = as_user(seeded["viewer"])
    assert client.get("/api/v1/tickets", headers=headers).status_code == 200
    created = client.post("/api/v1/tickets", json={"title": "abc", "description": "x"}, headers=headers)
    assert created.status_code == 403
    assert "tickets:write" in created.json()["detail"]


@pytest.mark.parametrize(("who", "expected"), [("admin", 204), ("maria", 403), ("agent", 403), ("viewer", 403)])
def test_delete_user_requires_admin(client, seeded, who, expected):
    target = seeded["agent"] if who != "agent" else seeded["viewer"]
    response = client.delete(f"/api/v1/users/{target.id}", headers=as_user(seeded[who]))
    assert response.status_code == expected


def test_deleted_user_loses_access_immediately(client, seeded):
    victim_headers = as_user(seeded["agent"])
    login(client, "agent@opspilot.dev")
    assert client.delete(f"/api/v1/users/{seeded['agent'].id}", headers=as_user(seeded["admin"])).status_code == 204
    assert client.get("/api/v1/tickets", headers=victim_headers).status_code == 401
    assert client.post(f"{AUTH}/refresh").status_code == 401
    assert login(client, "agent@opspilot.dev").status_code == 401


def test_admin_cannot_delete_or_demote_self(client, seeded):
    admin = seeded["admin"]
    assert client.delete(f"/api/v1/users/{admin.id}", headers=as_user(admin)).status_code == 422
    demote = client.patch(f"/api/v1/users/{admin.id}", json={"role": "viewer"}, headers=as_user(admin))
    assert demote.status_code == 422


def test_admin_user_management(client, seeded):
    headers = as_user(seeded["admin"])
    created = client.post(
        "/api/v1/users",
        json={"email": "new@opspilot.dev", "full_name": "New Hire", "role": "support_agent",
              "password": "long-enough-password"},
        headers=headers,
    )  # fmt: skip
    assert created.status_code == 201 and created.json()["role"] == "support_agent"
    user_id = created.json()["id"]

    duplicate = client.post(
        "/api/v1/users",
        json={"email": "NEW@opspilot.dev", "full_name": "Dup", "role": "viewer", "password": "long-enough-password"},
        headers=headers,
    )
    assert duplicate.status_code == 409

    promoted = client.patch(f"/api/v1/users/{user_id}", json={"role": "engineer"}, headers=headers)
    assert promoted.json()["role"] == "engineer"
    assert login(client, "new@opspilot.dev", "long-enough-password").json()["user"]["role"] == "engineer"

    bad_role = client.patch(f"/api/v1/users/{user_id}", json={"role": "superuser"}, headers=headers)
    assert bad_role.status_code == 422

    listing = client.get("/api/v1/users/admin", headers=headers).json()
    assert "new@opspilot.dev" in [u["email"] for u in listing]
    assert client.get("/api/v1/users/admin", headers=as_user(seeded["maria"])).status_code == 403


def test_service_catalogue_is_admin_managed(client, seeded):
    body = {"name": "search-service", "display_name": "Search", "tier": 2, "owner_team": "Discovery",
            "dependencies": ["auth-service"]}  # fmt: skip
    assert client.post("/api/v1/services", json=body, headers=as_user(seeded["maria"])).status_code == 403
    created = client.post("/api/v1/services", json=body, headers=as_user(seeded["admin"]))
    assert created.status_code == 201 and created.json()["dependencies"] == ["auth-service"]
