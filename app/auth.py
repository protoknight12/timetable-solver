"""Login, roles, CSRF and rate limiting.

Roles: "admin" (Администратор) may change data, "viewer" (Преглед) may only read.
Every route that changes data must use ``@admin_required``; a test walks the
URL map and fails for any write route that does not.
"""

from __future__ import annotations

import os
from functools import wraps

import click
from flask import abort
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from app.models import ROLES, User, db

csrf = CSRFProtect()
limiter = Limiter(get_remote_address)
login_manager = LoginManager()
login_manager.login_message = "Моля, влезте в системата."
login_manager.login_message_category = "warning"

MIN_PASSWORD_LENGTH = 8
# Checked when the user does not exist, so timing does not reveal valid usernames.
_DUMMY_HASH = generate_password_hash("dummy-password")


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return db.session.get(User, int(user_id))


def admin_required(view):
    """Anonymous -> redirect to the login page; viewer -> 403."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if current_user.role != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def authenticate(username: str, password: str) -> User | None:
    user = db.session.scalar(select(User).where(User.username == username))
    ok = check_password_hash(user.password_hash if user else _DUMMY_HASH, password)
    return user if user and ok else None


def create_user(username: str, password: str, role: str) -> User:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"password must have at least {MIN_PASSWORD_LENGTH} characters"
        )
    if db.session.scalar(select(User).where(User.username == username)):
        raise ValueError(f"user {username!r} already exists")
    user = User(
        username=username, password_hash=generate_password_hash(password), role=role
    )
    db.session.add(user)
    db.session.commit()
    return user


@click.command("create-user")
@click.argument("username")
@click.option("--role", type=click.Choice(ROLES), required=True)
@click.option(
    "--password-env", help="Read the password from this environment variable."
)
def create_user_command(username: str, role: str, password_env: str | None) -> None:
    """Create a login account. The password is prompted unless --password-env is given."""
    if password_env:
        password = os.environ.get(password_env)
        if not password:
            raise click.ClickException(
                f"Environment variable {password_env} is not set."
            )
    else:
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    try:
        create_user(username, password, role)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"Created {role} account {username!r}.")


def create_demo_users() -> None:
    """Demo accounts for the jury. Passwords come from the environment, never the repo."""
    for username, role, var in (
        ("admin", "admin", "DEMO_ADMIN_PASSWORD"),
        ("preglad", "viewer", "DEMO_VIEWER_PASSWORD"),
    ):
        password = os.environ.get(var)
        if not password:
            click.echo(
                f"{var} is not set: skipping the {username!r} account.", err=True
            )
        elif db.session.scalar(select(User).where(User.username == username)):
            click.echo(f"Account {username!r} already exists: left unchanged.")
        else:
            create_user(username, password, role)
            click.echo(f"Created demo account {username!r}.")
