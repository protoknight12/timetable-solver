"""DB fixtures. Locally SQLite in memory; CI also runs them on PostgreSQL via TEST_DATABASE_URL."""

import os

import pytest
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
            "SQLALCHEMY_DATABASE_URI": os.environ.get("TEST_DATABASE_URL")
            or "sqlite://",
        }
    )
    with app.app_context():
        _wipe()
        yield app
        _wipe()


@pytest.fixture
def migrated(app):
    """An app whose schema was created by the real migrations."""
    upgrade(directory="migrations")
    return app
