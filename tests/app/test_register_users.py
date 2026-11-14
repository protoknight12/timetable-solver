"""Public sign-up (viewer only), logout button, and admin account management."""

from sqlalchemy import select

from app.auth import create_user
from app.models import User, db

GOOD = {"username": "maria", "password": "long-enough-pw", "confirm": "long-enough-pw"}


def _user(name):
    db.session.expire_all()
    return db.session.scalar(select(User).where(User.username == name))


def test_register_creates_a_viewer_who_can_log_in(migrated):
    client = migrated.test_client()
    # A role in the form is ignored: sign-up can never produce an admin.
    r = client.post("/register", data={**GOOD, "role": "admin"}, follow_redirects=True)
    assert "Регистрацията е успешна" in r.get_data(as_text=True)
    user = _user("maria")
    assert user.role == "viewer" and user.password_hash != GOOD["password"]
    login = {"username": "maria", "password": GOOD["password"]}
    assert client.post("/login", data=login).status_code == 302


def test_register_rejects_bad_input_and_saves_nothing(migrated):
    client = migrated.test_client()
    create_user("taken", "long-enough-pw", "admin")
    for bad in (
        {**GOOD, "username": "ab"},
        {**GOOD, "username": "has space"},
        {**GOOD, "username": "taken"},
        {**GOOD, "password": "short", "confirm": "short"},
        {**GOOD, "confirm": "different-pw-1"},
        {**GOOD, "username": ""},
    ):
        r = client.post("/register", data=bad)
        assert r.status_code == 400 and "invalid-feedback" in r.get_data(as_text=True)
    assert _user("maria") is None and _user("ab") is None


def test_register_needs_csrf_and_is_rate_limited(migrated):
    migrated.config["WTF_CSRF_ENABLED"] = True
    assert migrated.test_client().post("/register", data=GOOD).status_code == 400
    migrated.config["WTF_CSRF_ENABLED"] = False
    migrated.config["LOGIN_RATE_LIMIT"] = "2 per minute"
    client = migrated.test_client()
    codes = [client.post("/register", data={}).status_code for _ in range(4)]
    assert codes[-1] == 429


def test_logged_in_user_is_sent_away_from_register(make_client):
    r = make_client("viewer").get("/register")
    assert r.status_code == 302


def test_navbar_shows_logout_when_logged_in_and_login_otherwise(make_client, migrated):
    html = make_client("viewer").get("/").get_data(as_text=True)
    assert "Изход" in html and "Вход</a>" not in html
    html = migrated.test_client().get("/").get_data(as_text=True)
    assert "Вход</a>" in html and "Регистрация" in html and "Изход" not in html


def test_logout_logs_out(make_client):
    client = make_client("admin")
    assert client.get("/admin/users/").status_code == 200
    assert client.post("/logout").status_code == 302
    assert client.get("/admin/users/").status_code == 302


def test_user_pages_are_admin_only(make_client, migrated):
    viewer, anonymous = make_client("viewer"), migrated.test_client()
    for url in ("/admin/users/", "/admin/users/new"):
        assert viewer.get(url).status_code == 403
        assert anonymous.get(url).status_code == 302
    html = viewer.get("/").get_data(as_text=True)
    assert "Потребители" not in html


def test_admin_creates_users_with_a_role(make_client):
    admin = make_client("admin")
    assert "Потребители" in admin.get("/").get_data(as_text=True)
    r = admin.post(
        "/admin/users/new", data={**GOOD, "role": "admin"}, follow_redirects=True
    )
    assert "Потребителят е добавен" in r.get_data(as_text=True)
    assert _user("maria").role == "admin"
    r = admin.post("/admin/users/new", data={**GOOD, "role": "viewer"})
    assert "Потребителското име е заето" in r.get_data(as_text=True)
    r = admin.post("/admin/users/new", data={**GOOD, "username": "x1y", "role": "boss"})
    assert r.status_code == 200 and _user("x1y") is None


def test_admin_changes_roles_but_not_their_own(make_client):
    admin = make_client("admin")
    create_user("ivan", "long-enough-pw", "viewer")
    ivan, me = _user("ivan"), _user("user-admin")

    admin.post(f"/admin/users/{ivan.id}/role", data={"role": "admin"})
    assert _user("ivan").role == "admin"
    admin.post(f"/admin/users/{ivan.id}/role", data={"role": "viewer"})
    assert _user("ivan").role == "viewer"

    r = admin.post(
        f"/admin/users/{me.id}/role", data={"role": "viewer"}, follow_redirects=True
    )
    assert "собствената си роля" in r.get_data(as_text=True)
    assert _user("user-admin").role == "admin"
    assert (
        admin.post(f"/admin/users/{ivan.id}/role", data={"role": "boss"}).status_code
        == 400
    )


def test_viewer_cannot_change_roles(make_client):
    viewer = make_client("viewer")
    target = create_user("ivan", "long-enough-pw", "viewer")
    assert (
        viewer.post(
            f"/admin/users/{target.id}/role", data={"role": "admin"}
        ).status_code
        == 403
    )
    assert _user("ivan").role == "viewer"
