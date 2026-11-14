"""DB fixtures. Locally SQLite in memory; CI also runs them on PostgreSQL via TEST_DATABASE_URL."""

import os

import pytest
from flask import g
from flask_migrate import upgrade
from sqlalchemy import text

from app import create_app
from app.models import db


def _wipe():
    db.session.remove()
    db.drop_all()
    db.session.execute(text("DROP TABLE IF EXISTS alembic_version"))
    db.session.commit()
    db.session.remove()


@pytest.fixture
def app():
    app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,  # test_auth.py turns it back on where it matters
            "LOGIN_RATE_LIMIT": "1000 per minute",
            "SQLALCHEMY_DATABASE_URI": os.environ.get("TEST_DATABASE_URL")
            or "sqlite://",
        }
    )
    # The fixture's long-lived app context is shared by every request, so
    # Flask-Login's per-request user cache in `g` would leak between clients.
    app.before_request(lambda: g.pop("_login_user", None) and None)
    with app.app_context():
        _wipe()
        yield app
        _wipe()


@pytest.fixture
def make_client(migrated):
    """Factory: a test client logged in as a new user of the given role (or anonymous)."""
    from app.auth import create_user

    def make(role: str | None = None):
        client = migrated.test_client()
        if role:
            create_user(f"user-{role}", "test-password-1", role)
            data = {"username": f"user-{role}", "password": "test-password-1"}
            assert client.post("/login", data=data).status_code == 302
        return client

    return make


@pytest.fixture
def migrated(app):
    """An app whose schema was created by the real migrations."""
    upgrade(directory="migrations")
    return app
