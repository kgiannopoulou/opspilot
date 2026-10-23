"""Integration tests run against a real PostgreSQL database built by the Alembic migrations."""

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import get_db
from app.main import app
from app.models import Role, Service, User
from app.scripts.seed import reset

TEST_DATABASE_URL = os.environ.get(
    "OPSPILOT_TEST_DATABASE_URL", "postgresql+psycopg://opspilot:opspilot@localhost:5438/opspilot_test"
)


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
def client(session_factory) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.rollback()
            session.close()

    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(db: Session) -> dict[str, User | Service]:
    roles = {r.name: r.id for r in db.query(Role)}
    agent = User(email="agent@opspilot.dev", full_name="Sam Agent", role_id=roles["support_agent"])
    maria = User(email="maria@opspilot.dev", full_name="Maria Engineer", role_id=roles["engineer"])
    payment = Service(name="payment-service", display_name="Payment Service", tier=1, owner_team="Payments")
    auth = Service(name="auth-service", display_name="Authentication Service", tier=1, owner_team="Identity")
    db.add_all([agent, maria, payment, auth])
    db.commit()
    return {"agent": agent, "maria": maria, "payment": payment, "auth": auth}


def as_user(user: User) -> dict[str, str]:
    return {"X-User-Email": user.email}
