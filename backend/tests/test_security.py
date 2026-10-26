"""Security controls (docs/security.md): headers, body limits, rate limits, client IPs, passwords, redaction."""

import logging
import time

import pytest

from app.auth.rate_limit import RateLimiter
from app.core.client_ip import resolve_client_ip
from app.core.config import Settings, get_settings
from app.core.http_security import API_CSP, security_headers
from app.core.redaction import MASK, RedactingFilter, redact
from app.core.security import BCRYPT_MAX_BYTES, hash_password, verify_password
from app.schemas.auth import UserCreate
from tests.conftest import PASSWORD, as_user

AUTH = "/api/v1/auth"

# --- secure headers -------------------------------------------------------------------------------------------


def test_security_headers_are_strict_for_the_api_and_narrow_for_docs():
    api = security_headers("/api/v1/tickets", hsts=False)
    assert api["Content-Security-Policy"] == API_CSP
    assert api["X-Frame-Options"] == "DENY" and api["X-Content-Type-Options"] == "nosniff"
    assert "Strict-Transport-Security" not in api
    docs = security_headers("/docs", hsts=True)
    assert "cdn.jsdelivr.net" in docs["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in docs["Content-Security-Policy"]
    assert docs["Strict-Transport-Security"].startswith("max-age=")


def test_every_response_carries_security_headers(client, seeded):
    for response in (
        client.get("/health"),
        client.get("/api/v1/tickets"),  # 401
        client.get("/api/v1/tickets", headers=as_user(seeded["agent"])),
        client.get("/api/v1/nope", headers=as_user(seeded["agent"])),  # 404
    ):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert "default-src 'none'" in response.headers["content-security-policy"]
    assert client.get("/api/v1/tickets", headers=as_user(seeded["agent"])).headers["cache-control"] == "no-store"


def test_oversized_bodies_are_rejected_before_parsing(client, seeded, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_body_bytes", 1_000)
    response = client.post(
        "/api/v1/tickets", headers=as_user(seeded["agent"]), json={"title": "x" * 50, "description": "y" * 5_000}
    )
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"


# --- input validation & password handling ---------------------------------------------------------------------


def test_validation_errors_never_echo_a_password(client, seeded):
    response = client.post(f"{AUTH}/login", json={"email": "a@opspilot.dev", "password": "s3cret-value" * 30})
    assert response.status_code == 422
    assert "s3cret-value" not in response.text


def test_password_longer_than_bcrypt_input_is_a_failed_login_not_a_500(client, seeded):
    response = client.post(f"{AUTH}/login", json={"email": "agent@opspilot.dev", "password": PASSWORD + "x" * 80})
    assert response.status_code == 401


def test_verify_password_handles_over_long_input():
    stored = hash_password("a" * BCRYPT_MAX_BYTES)
    assert verify_password("a" * BCRYPT_MAX_BYTES, stored)
    assert not verify_password("a" * (BCRYPT_MAX_BYTES + 1), stored)  # bcrypt would truncate: must not match
    assert not verify_password("a" * 100, None)


@pytest.mark.parametrize(
    ("password", "ok"),
    [("correct-horse-battery", True), ("short", False), ("aaaaaaaaaaaaaaaa", False), ("ü" * 40, False)],
)
def test_new_password_policy(password, ok):
    data = {"email": "new@opspilot.dev", "full_name": "New User", "role": "viewer", "password": password}
    if ok:
        UserCreate(**data)
    else:
        with pytest.raises(ValueError):
            UserCreate(**data)


# --- secrets via environment ----------------------------------------------------------------------------------


def test_insecure_configuration_is_refused():
    with pytest.raises(ValueError, match="CORS"):
        Settings(cors_origins=["*"])
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        Settings(environment="production", jwt_secret="x" * 40)
    assert Settings(environment="production", jwt_secret="x" * 40, cookie_secure=True).cookie_secure


# --- rate limiting --------------------------------------------------------------------------------------------


def test_fixed_window_limiter(throttle):
    limiter = RateLimiter(throttle.client)
    results = [limiter.hit("k", limit=3, window=60, now=1_000_040.0) for _ in range(4)]
    assert [allowed for allowed, *_ in results] == [True, True, True, False]
    assert results[0][1] == 2 and results[0][2] == 40  # remaining, seconds to the next window
    assert limiter.hit("k", limit=3, window=60, now=1_000_080.0)[0]  # next window


def test_limiter_fails_open_without_redis():
    import redis

    dead = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
    assert RateLimiter(dead).hit("k", 1, 60)[0]


def test_ai_endpoints_are_rate_limited_per_user(client, seeded, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_rate_limit_per_minute", 2)
    agent, maria = as_user(seeded["agent"]), as_user(seeded["maria"])
    statuses = [client.get("/api/v1/knowledge/search?q=timeout", headers=agent).status_code for _ in range(3)]
    assert statuses == [200, 200, 429]
    limited = client.get("/api/v1/knowledge/search?q=timeout", headers=agent)
    assert int(limited.headers["retry-after"]) >= 1
    assert client.get("/api/v1/knowledge/search?q=timeout", headers=maria).status_code == 200  # per user


def test_whole_api_is_rate_limited_per_client(client, seeded, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 3)
    headers = as_user(seeded["viewer"])
    assert [client.get("/api/v1/tickets", headers=headers).status_code for _ in range(4)] == [200, 200, 200, 429]
    assert client.get("/health").status_code == 200  # probes are outside the API router


def test_login_is_rate_limited_per_client_across_accounts(client, seeded, monkeypatch):
    """Credential stuffing: one password tried against many accounts never trips a per-account lockout."""
    monkeypatch.setattr(get_settings(), "login_rate_limit_per_minute", 3)
    statuses = [
        client.post(f"{AUTH}/login", json={"email": f"user{i}@opspilot.dev", "password": "Winter2026!"}).status_code
        for i in range(4)
    ]
    assert statuses == [401, 401, 401, 429]


# --- client IP behind a proxy ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("peer", "forwarded", "trusted", "expected"),
    [
        ("203.0.113.9", None, (), "203.0.113.9"),
        ("203.0.113.9", "1.2.3.4", (), "203.0.113.9"),  # untrusted peer: header ignored (spoofing)
        ("172.30.0.10", "198.51.100.7", ("172.30.0.10/32",), "198.51.100.7"),
        ("172.30.0.10", "6.6.6.6, 198.51.100.7", ("172.30.0.10/32",), "198.51.100.7"),  # client-written left part
        ("172.30.0.10", "198.51.100.7, 10.0.0.2", ("172.30.0.10/32", "10.0.0.0/8"), "198.51.100.7"),
        ("172.30.0.10", "garbage", ("172.30.0.10/32",), "172.30.0.10"),
        ("testclient", None, (), None),
    ],
)
def test_forwarded_for_is_only_believed_from_trusted_proxies(peer, forwarded, trusted, expected):
    assert resolve_client_ip(peer, forwarded, trusted) == expected


# --- log redaction --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "leaked"),
    [
        ("Authorization: Bearer abcdefghijklmnop.qrstu", "abcdefghijklmnop"),
        ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl", "eyJhbGciOiJIUzI1NiJ9"),
        ("connecting to postgresql+psycopg://opspilot:hunter2@db:5432/x", "hunter2"),
        ("login payload password=Winter2026! user=x", "Winter2026!"),
        ('{"api_key": "sk-ant-api03-abcdefghijklmnopqrstuv"}', "sk-ant-api03"),
        ("failed login for maria.papadopoulou@opspilot.dev", "maria.papadopoulou"),
    ],
)
def test_redaction(text, leaked):
    assert leaked not in redact(text)


def test_redaction_keeps_the_useful_part():
    assert redact("failed login for maria@opspilot.dev") == "failed login for m***@opspilot.dev"
    assert redact("payment-service p95 5321 ms order_id=ORD-1") == "payment-service p95 5321 ms order_id=ORD-1"


def test_log_filter_redacts_messages_and_structured_fields():
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "refresh with %s", ("token=abc123def456",), None)
    record.authorization = "Bearer secretsecret"
    record.route = "/api/v1/auth/refresh"
    RedactingFilter().filter(record)
    assert record.getMessage() == f"refresh with token={MASK}"
    assert record.authorization == MASK
    assert record.route == "/api/v1/auth/refresh"


# --- SQL injection --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "'; DROP TABLE tickets; --",
        "' OR '1'='1",
        "1; SELECT pg_sleep(5)",
        "%' UNION SELECT password_hash FROM users --",
    ],
)
def test_hostile_search_input_is_data_not_sql(client, seeded, db, payload):
    headers = as_user(seeded["agent"])
    client.post("/api/v1/tickets", headers=headers, json={"title": "Checkout fails", "description": "Card declined"})
    started = time.perf_counter()
    response = client.get("/api/v1/tickets", params={"q": payload}, headers=headers)
    assert response.status_code == 200 and response.json()["total"] == 0  # matched literally, so nothing matches
    assert time.perf_counter() - started < 2  # pg_sleep did not run
    assert "password_hash" not in response.text
    assert client.get("/api/v1/tickets", headers=headers).json()["total"] == 1  # the table is still there
