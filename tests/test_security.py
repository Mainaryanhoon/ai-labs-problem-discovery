from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import security
from app.db import Base, get_db
from app.main import app


def test_api_key_uses_constant_time_comparison_contract():
    assert security.api_key_matches("expected-test-key", "expected-test-key")
    assert not security.api_key_matches("wrong-test-key", "expected-test-key")
    assert not security.api_key_matches(None, "expected-test-key")
    assert not security.api_key_matches("provided", "")


def test_sliding_window_limiter_expires_old_events_and_bounds_clients():
    now = [0.0]
    limiter = security.SlidingWindowLimiter(
        limit=2,
        window=60,
        clock=lambda: now[0],
    )

    assert limiter.allow("client-a")
    assert limiter.allow("client-a")
    assert not limiter.allow("client-a")
    now[0] = 60.0
    assert limiter.allow("client-a")


def test_api_key_protection_is_opt_in_and_health_stays_public(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)

    def override_get_db():
        with session_factory() as session:
            yield session

    configured_settings = SimpleNamespace(
        service_api_key="expected-test-key",
        submission_rate_limit_per_minute=0,
    )
    monkeypatch.setattr(security, "get_settings", lambda: configured_settings)
    monkeypatch.setattr("app.main.get_settings", lambda: configured_settings)
    monkeypatch.setattr(security, "_limiter", None)
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            protected = client.get("/api/sources/yield")
            assert protected.status_code == 401
            assert "expected-test-key" not in protected.text
            protected_schema = client.get("/openapi.json")
            assert protected_schema.status_code == 401
            allowed = client.get(
                "/api/sources/yield",
                headers={"X-API-Key": "expected-test-key"},
            )
            assert allowed.status_code == 200
            allowed_schema = client.get(
                "/openapi.json",
                headers={"X-API-Key": "expected-test-key"},
            )
            assert allowed_schema.status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_submission_rate_limit_is_applied_at_http_boundary(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)

    def override_get_db():
        with session_factory() as session:
            yield session

    configured_settings = SimpleNamespace(
        service_api_key="",
        submission_rate_limit_per_minute=1,
    )
    monkeypatch.setattr(security, "get_settings", lambda: configured_settings)
    monkeypatch.setattr("app.main.get_settings", lambda: configured_settings)
    monkeypatch.setattr(security, "_limiter", None)
    app.dependency_overrides[get_db] = override_get_db
    payload = {"text": "A sufficiently detailed test submission."}
    try:
        with TestClient(app) as client:
            assert client.post("/api/submissions", json=payload).status_code == 201
            limited = client.post("/api/submissions", json=payload)
            assert limited.status_code == 429
            assert "key" not in limited.text.lower()
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
