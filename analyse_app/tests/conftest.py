"""Test scaffolding for analyse_app.

Hermetic + offline: in-memory SQLite with a StaticPool (so rows written in one
request are visible to the next — bare sqlite :memory: gives each connection a
private db), AUTH_MODE=off (dev-SU blob with session_phases:["analysis"]), and
no real sockets (the CDR fan-out + SSO revalidation + ips consent join are all
patched by the individual tests).
"""
import os

import pytest
import sqlalchemy

# Set config env before importing the app.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("AUTH_MODE", "off")

from app import create_app  # noqa: E402
from app.extensions import db as _db  # noqa: E402


def make_app(**overrides):
    """Build a hermetic app with an isolated in-memory DB (StaticPool)."""
    cfg = {
        "TESTING": True,
        "AUTH_MODE": "off",
        "SECRET_KEY": "test-secret",
        "DATABASE_URL": "sqlite:///:memory:",
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SQLALCHEMY_ENGINE_OPTIONS": {
            "connect_args": {"check_same_thread": False},
            "poolclass": sqlalchemy.pool.StaticPool,
        },
    }
    cfg.update(overrides)
    app = create_app(cfg)
    with app.app_context():
        _db.create_all()
    return app


@pytest.fixture(autouse=True)
def _allow_all_consent(monkeypatch):
    """The cohort routes join every member set against ips.pdhc's
    analysis-filter and fail closed (503) when it's unreachable. These tests
    exercise the federation/gate flow, not consent — patch the client to an
    allow-all verdict (same seam dashboard.pdhc patches)."""
    from app.routes import researcher as r
    fake = type("C", (), {"analysis_filter": staticmethod(
        lambda guids, purpose, projects=None: {
            "allowed": list(guids), "excluded": []})})()
    monkeypatch.setattr(r, "_ips_client", lambda: fake)


@pytest.fixture
def app():
    return make_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    with app.app_context():
        yield _db
