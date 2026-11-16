"""BE-20: a viewer login can be linked to a teacher."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import create_user
from app.models import Teacher, User, db


@pytest.fixture
def setup(migrated):
    teachers = [Teacher(name="Иван"), Teacher(name="Мария")]
    db.session.add_all(teachers)
    db.session.commit()
    for name in ("ivan", "maria"):
        create_user(name, "test-password-1", "viewer")
    return [t.id for t in teachers]


def _user(name):
    db.session.expire_all()
    return db.session.scalar(select(User).where(User.username == name))


def _link(admin, teacher_id, username):
    uid = _user(username).id if username else ""
    r = admin.post(
        f"/data/teachers/{teacher_id}/edit", data={"name": "Иван", "account": str(uid)}
    )
    assert r.status_code == 302


def test_admin_links_and_unlinks(make_client, setup):
    admin = make_client("admin")
    _link(admin, setup[0], "ivan")
    assert _user("ivan").teacher_id == setup[0]
    assert "ivan" in admin.get("/admin/users/").text
    # Swapping the login of a teacher must not trip the unique constraint.
    _link(admin, setup[0], "maria")
    assert _user("maria").teacher_id == setup[0] and _user("ivan").teacher_id is None
    _link(admin, setup[0], None)
    assert _user("maria").teacher_id is None


def test_form_offers_only_free_viewers(make_client, setup):
    admin = make_client("admin")  # user-admin is not offered: wrong role
    _link(admin, setup[0], "ivan")
    html = admin.get(f"/data/teachers/{setup[1]}/edit").text
    assert "maria" in html and "Без профил" in html
    assert "ivan" not in html and "user-admin" not in html
    # The linked one is offered, preselected, on its own teacher's page.
    assert "ivan" in admin.get(f"/data/teachers/{setup[0]}/edit").text
    # A forged value (taken login, admin) is rejected, nothing changes.
    for bad in (_user("ivan").id, _user("user-admin").id):
        r = admin.post(
            f"/data/teachers/{setup[1]}/edit", data={"name": "М", "account": str(bad)}
        )
        assert r.status_code == 200
    assert _user("ivan").teacher_id == setup[0]


def test_unique_per_teacher(setup):
    _user("ivan").teacher_id = setup[0]
    db.session.commit()
    _user("maria").teacher_id = setup[0]
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_deleting_teacher_unlinks(make_client, setup):
    admin = make_client("admin")
    _link(admin, setup[0], "ivan")
    assert admin.post(f"/data/teachers/{setup[0]}/delete").status_code == 302
    assert _user("ivan").teacher_id is None


def test_login_lands_on_own_timetable(migrated, setup):
    _user("ivan").teacher_id = setup[0]
    db.session.commit()
    own = f"/timetable?teacher={setup[0]}"
    for name, target in (("ivan", own), ("maria", "/")):
        client = migrated.test_client()
        r = client.post(
            "/login", data={"username": name, "password": "test-password-1"}
        )
        assert r.headers["Location"].endswith(target)
    html = client.get(own).text  # maria: no link in the navbar
    assert "Моето разписание" not in html
    ivan = migrated.test_client()
    ivan.post("/login", data={"username": "ivan", "password": "test-password-1"})
    assert "Моето разписание" in ivan.get(own).text
    # Other timetables stay open.
    assert ivan.get(f"/timetable?teacher={setup[1]}").status_code == 200


def test_viewer_cannot_change_link(make_client, setup):
    viewer = make_client("viewer")
    r = viewer.post(
        f"/data/teachers/{setup[0]}/edit", data={"name": "X", "account": "1"}
    )
    assert r.status_code == 403
    assert viewer.get(f"/data/teachers/{setup[0]}/edit").status_code == 403
