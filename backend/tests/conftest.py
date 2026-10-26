"""Integration tests run against a real PostgreSQL database built by the Alembic migrations."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import redis
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, make_url, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.auth.rate_limit import LoginThrottle, get_login_throttle, get_redis
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import Incident, Role, Service, User
from app.scripts.seed import reset, seed
from app.workers.worker import Worker

TEST_DATABASE_URL = os.environ.get(
    "OPSPILOT_TEST_DATABASE_URL", "postgresql+psycopg://opspilot:opspilot@localhost:5438/opspilot_test"
)
TEST_REDIS_URL = os.environ.get("OPSPILOT_TEST_REDIS_URL", "redis://localhost:6381/15")
PASSWORD = "correct-horse-battery"
# Tests that touch PostgreSQL or Redis are integration tests; everything else is a unit test (no services needed).
INTEGRATION_FIXTURES = {"engine", "session_factory", "db", "throttle", "worker", "client", "seeded", "demo"}
PASSWORD_HASH = hash_password(PASSWORD)  # bcrypt is slow on purpose; hash once per test run


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """`pytest -m unit` runs in seconds without Docker; CI runs both."""
    for item in items:
        uses_services = INTEGRATION_FIXTURES & set(getattr(item, "fixturenames", ()))
        item.add_marker(pytest.mark.integration if uses_services else pytest.mark.unit)


def _ensure_database(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target.database})
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def engine():
    _ensure_database(TEST_DATABASE_URL)
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    cfg.attributes["database_url"] = TEST_DATABASE_URL
    cfg.attributes["configure_logger"] = False
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    eng = create_engine(TEST_DATABASE_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine) -> Iterator[sessionmaker[Session]]:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    with factory() as session:
        reset(session)


@pytest.fixture
def db(session_factory) -> Iterator[Session]:
    session = session_factory()
    yield session
    session.close()


@pytest.fixture
def throttle() -> Iterator[LoginThrottle]:
    client = redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    client.flushdb()
    yield LoginThrottle(client)
    client.flushdb()


@pytest.fixture
def worker(session_factory, throttle) -> Worker:
    """The background worker, wired to the test database and Redis; call .drain() to process queued jobs."""
    return Worker(session_factory, throttle.client, name="test-worker")


@pytest.fixture
def client(session_factory, throttle) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.rollback()
            session.close()

    app.dependency_overrides[get_db] = override
    app.dependency_overrides[get_login_throttle] = lambda: throttle
    app.dependency_overrides[get_redis] = lambda: throttle.client
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(db: Session) -> dict[str, User | Service]:
    roles = {r.name: r.id for r in db.query(Role)}

    def user(email: str, name: str, role: str) -> User:
        return User(email=email, full_name=name, role_id=roles[role], password_hash=PASSWORD_HASH)

    people = {
        "agent": user("agent@opspilot.dev", "Sam Agent", "support_agent"),
        "maria": user("maria@opspilot.dev", "Maria Engineer", "engineer"),
        "admin": user("admin@opspilot.dev", "Nikos Admin", "admin"),
        "viewer": user("viewer@opspilot.dev", "Dana Viewer", "viewer"),
    }
    payment = Service(name="payment-service", display_name="Payment Service", tier=1, owner_team="Payments")
    auth = Service(name="auth-service", display_name="Authentication Service", tier=1, owner_team="Identity")
    db.add_all([*people.values(), payment, auth])
    db.commit()
    return people | {"payment": payment, "auth": auth}


@pytest.fixture
def demo(db: Session) -> tuple[dict[str, User], dict[str, str]]:
    """The full demo seed: users by e-mail local part, incident numbers by title."""
    seed(db, now=datetime.now(UTC))
    users = {u.email.split("@")[0]: u for u in db.query(User)}
    incidents = {i.title: i.number for i in db.scalars(select(Incident)).unique()}
    return users, incidents


def as_user(user: User) -> dict[str, str]:
    """Bearer header for `user`, minted directly (login itself is covered in test_auth.py)."""
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.name)[0]}"}
