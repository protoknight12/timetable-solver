import os
import secrets
import sqlite3

from flask import Flask
from flask_migrate import Migrate
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.auth import create_user_command, csrf, limiter, login_manager
from app.errors import register_error_pages
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
        SOLVER_TIME_LIMIT_S=int(os.environ.get("SOLVER_TIME_LIMIT_S") or 60),
        SOLVE_RATE_LIMIT="5 per minute",
        REDIS_URL=os.environ.get("REDIS_URL") or "redis://localhost:6379/0",
        # Tests replace this with a callable(job_id, timeout_s); None means RQ.
        SOLVE_ENQUEUE=None,
        LOGIN_RATE_LIMIT="5 per minute",
        # Redis in production; if it is down, fall back to per-process memory.
        RATELIMIT_STORAGE_URI=os.environ.get("REDIS_URL") or "memory://",
        RATELIMIT_IN_MEMORY_FALLBACK_ENABLED=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if config:
        app.config.update(config)
    # Secure cookies need HTTPS: off only in debug and tests (or when set explicitly).
    if "SESSION_COOKIE_SECURE" not in (config or {}):
        app.config["SESSION_COOKIE_SECURE"] = not (app.debug or app.testing)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "pages.login"
    register_error_pages(app)

    from app.cli import seed_command

    app.cli.add_command(seed_command)
    app.cli.add_command(create_user_command)
    from app.routes.data import bp as data_bp
    from app.routes.solve import bp as solve_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(solve_bp)
    app.register_blueprint(data_bp)

    return app
