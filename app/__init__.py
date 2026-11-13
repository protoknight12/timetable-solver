import os
import secrets
import sqlite3

from flask import Flask
from flask_migrate import Migrate
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.models import db
from app.routes.pages import bp as pages_bp

migrate = Migrate()


@event.listens_for(Engine, "connect")
def _sqlite_foreign_keys(dbapi_connection, _record):
    # SQLite ignores foreign keys (and ON DELETE CASCADE) unless asked.
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        # A random key per process is safe but logs everyone out on restart and
        # breaks with several gunicorn workers: production must set SECRET_KEY.
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        # Without DATABASE_URL: throwaway in-memory SQLite (tests, frontend work).
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL") or "sqlite://",
        SCHOOL_DAYS=5,
    )
    if config:
        app.config.update(config)

    db.init_app(app)
    migrate.init_app(app, db)

    from app.cli import seed_command

    app.cli.add_command(seed_command)
    app.register_blueprint(pages_bp)

    return app
