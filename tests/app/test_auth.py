import pytest
from sqlalchemy import select

from app import create_app
from app.auth import admin_required, create_demo_users, create_user, create_user_command
from app.cli import seed_command
from app.models import User, db

GENERIC_ERROR = "Грешно потребителско име или парола."


@pytest.mark.parametrize("role", ["admin", "viewer"])
def test_login_ok_and_logout(make_client, role):
    client = make_client(role)  # asserts the login redirect
    assert client.post("/logout").status_code == 302


def test_login_bad_credentials_give_one_generic_message(migrated):
    create_user("ivan", "right-password", "admin")
    client = migrated.test_client()
    for data in (
        {"username": "ivan", "password": "wrong"},
        {"username": "nobody", "password": "right-password"},
    ):
        r = client.post("/login", data=data)
        assert r.status_code == 401
        assert GENERIC_ERROR in r.get_data(as_text=True)


def test_logout_ends_the_session(make_client):
    client = make_client("admin")
    client.post("/logout")
    # Write routes now redirect to the login page (see the url_map test below).
    assert client.get("/login").status_code == 200


def test_csrf_token_required(migrated):
    migrated.config["WTF_CSRF_ENABLED"] = True
    client = migrated.test_client()
    assert 'name="csrf_token"' in client.get("/login").get_data(as_text=True)
    r = client.post("/login", data={"username": "a", "password": "b"})
    assert r.status_code == 400
    assert client.post("/logout").status_code == 400


def test_login_is_rate_limited(migrated):
    migrated.config["LOGIN_RATE_LIMIT"] = "3 per minute"
    client = migrated.test_client()
    codes = [
        client.post("/login", data={"username": "a", "password": "b"}).status_code
        for _ in range(5)
    ]
    assert codes == [401, 401, 401, 429, 429]
    assert "Твърде много" in client.post("/login").get_data(as_text=True)


def test_error_pages_are_bulgarian_and_use_base(migrated):
    r = migrated.test_client().get("/няма-такава")
    html = r.get_data(as_text=True)
    assert r.status_code == 404 and "Страницата не е намерена" in html
    assert 'id="theme-toggle"' in html


def test_session_cookie_flags(migrated):
    migrated.config["SESSION_COOKIE_SECURE"] = True
    create_user("ivan", "right-password", "admin")
    r = migrated.test_client().post(
        "/login", data={"username": "ivan", "password": "right-password"}
    )
    cookie = r.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" in cookie
    # Default: Secure everywhere except debug and tests.
    assert create_app().config["SESSION_COOKIE_SECURE"] is True
    assert create_app({"TESTING": True}).config["SESSION_COOKIE_SECURE"] is False


# Endpoints every role may POST to. Anything else that writes needs @admin_required.
PUBLIC_WRITE_ENDPOINTS = {"pages.login", "pages.logout"}


def _write_urls(app):
    for rule in app.url_map.iter_rules():
        methods = rule.methods & {"POST", "PUT", "PATCH", "DELETE"}
        if methods and rule.endpoint not in PUBLIC_WRITE_ENDPOINTS:
            url = rule.rule
            for arg in rule.arguments:
                for conv in ("", "int:", "string:", "path:", "uuid:", "float:"):
                    url = url.replace(f"<{conv}{arg}>", "1")
            yield min(methods), url


def test_every_write_route_is_admin_only(make_client, migrated):
    viewer, anonymous = make_client("viewer"), migrated.test_client()
    for method, url in _write_urls(migrated):
        assert viewer.open(url, method=method).status_code == 403, url
        r = anonymous.open(url, method=method)
        assert r.status_code == 302 and "/login" in r.headers["Location"], url


def test_url_map_walk_sees_new_write_routes(migrated):
    @migrated.post("/oops")
    def oops():
        return "changed"

    assert ("POST", "/oops") in list(_write_urls(migrated))


def test_admin_required_lets_admin_through(make_client, migrated):
    @migrated.post("/ok")
    @admin_required
    def ok():
        return "done"

    assert make_client("admin").post("/ok").get_data(as_text=True) == "done"
    assert make_client("viewer").post("/ok").status_code == 403


def test_create_user_command(migrated, monkeypatch):
    runner = migrated.test_cli_runner()
    monkeypatch.setenv("NEW_PW", "long-enough-pw")
    monkeypatch.setenv("SHORT_PW", "abc")
    args = ["maria", "--role", "viewer", "--password-env", "NEW_PW"]
    assert runner.invoke(create_user_command, args).exit_code == 0
    user = db.session.scalar(select(User).where(User.username == "maria"))
    assert user.role == "viewer" and user.password_hash != "long-enough-pw"
    for bad in (
        ["maria", "--role", "admin", "--password-env", "NEW_PW"],  # duplicate
        ["x", "--role", "boss", "--password-env", "NEW_PW"],  # unknown role
        ["y", "--role", "admin", "--password-env", "MISSING_VAR"],  # unset variable
        ["z", "--role", "admin", "--password-env", "SHORT_PW"],  # too short
    ):
        assert runner.invoke(create_user_command, bad).exit_code != 0
    prompted = runner.invoke(
        create_user_command,
        ["prompted", "--role", "admin"],
        input="prompt-pw-123\nprompt-pw-123\n",
    )
    assert prompted.exit_code == 0, prompted.output


def test_seed_creates_demo_accounts_from_env(migrated, monkeypatch):
    monkeypatch.setenv("DEMO_ADMIN_PASSWORD", "demo-admin-pw")
    monkeypatch.delenv("DEMO_VIEWER_PASSWORD", raising=False)
    assert migrated.test_cli_runner().invoke(seed_command).exit_code == 0
    roles = lambda: {u.username: u.role for u in db.session.scalars(select(User))}
    assert roles() == {"admin": "admin"}  # no password set for the viewer: skipped
    monkeypatch.setenv("DEMO_VIEWER_PASSWORD", "demo-viewer-pw")
    create_demo_users()
    assert roles() == {"admin": "admin", "preglad": "viewer"}
    # --reset replaces school data but keeps accounts.
    assert migrated.test_cli_runner().invoke(seed_command, ["--reset"]).exit_code == 0
    assert roles() == {"admin": "admin", "preglad": "viewer"}
