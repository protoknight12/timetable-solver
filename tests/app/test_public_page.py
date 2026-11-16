"""FE-16: public read-only class page and QR codes."""

import pytest
from sqlalchemy import select

from app import models as m
from app.models import db
from tests.app.test_timetable_page import _solved


def _enable(client, class_id):
    assert (
        client.post(
            f"/data/classes/{class_id}/public", data={"action": "on"}
        ).status_code
        == 302
    )
    db.session.expire_all()
    return db.session.get(m.SchoolClass, class_id).public_token


def _two_classes():
    return db.session.scalars(select(m.SchoolClass).order_by(m.SchoolClass.id)).all()[
        :2
    ]


def test_page_shows_only_its_class_and_no_admin_links(make_client, migrated):
    job_id = _solved()
    admin = make_client("admin")
    a, b = _two_classes()
    token = _enable(admin, a.id)
    # Nothing published: a notice, never the draft.
    r = migrated.test_client().get(f"/p/{token}")
    assert r.status_code == 200 and "Разписанието още не е публикувано." in r.text
    assert "Понеделник" not in r.text
    admin.post(f"/jobs/{job_id}/publish")
    r = migrated.test_client().get(f"/p/{token}")  # anonymous
    html = r.text
    assert r.status_code == 200 and "Понеделник" in html and a.name in html
    assert f"Разписание на {b.name}" not in html
    for admin_url in (
        "/timetable",
        "/lessons/",
        "/publish",
        "/data/classes/",
        "/login",
    ):
        assert f'href="{admin_url}' not in html and f'action="{admin_url}' not in html
    assert "draggable" not in html and "csrf_token" not in html
    assert 'id="theme-toggle"' in html


def test_unknown_and_disabled_tokens_give_the_same_404(make_client, migrated):
    _solved()
    admin = make_client("admin")
    a, _ = _two_classes()
    token = _enable(admin, a.id)
    anon = migrated.test_client()
    assert anon.get(f"/p/{token}").status_code == 200
    admin.post(f"/data/classes/{a.id}/public", data={"action": "off"})
    disabled, unknown = anon.get(f"/p/{token}"), anon.get("/p/no-such-token")
    assert disabled.status_code == unknown.status_code == 404
    assert disabled.text == unknown.text


def test_new_link_kills_the_old_one(make_client, migrated):
    _solved()
    admin = make_client("admin")
    a, _ = _two_classes()
    old = _enable(admin, a.id)
    admin.post(f"/data/classes/{a.id}/public", data={"action": "new"})
    db.session.expire_all()
    new = db.session.get(m.SchoolClass, a.id).public_token
    anon = migrated.test_client()
    assert new and new != old
    assert anon.get(f"/p/{old}").status_code == 404
    assert anon.get(f"/p/{new}").status_code == 200


def test_tokens_are_unique_and_url_safe(make_client, migrated):
    _solved()
    admin = make_client("admin")
    tokens = [_enable(admin, c.id) for c in _two_classes()]
    assert len(set(tokens)) == 2 and all(len(t) <= 32 for t in tokens)


def test_public_page_is_read_only_and_rate_limited(make_client, migrated):
    _solved()
    a, _ = _two_classes()
    token = _enable(make_client("admin"), a.id)
    migrated.config["PUBLIC_RATE_LIMIT"] = "2 per minute"
    anon = migrated.test_client()
    assert anon.post(f"/p/{token}").status_code == 405
    codes = [anon.get(f"/p/{token}").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    # No write route is public: every /p/ rule is GET only.
    for rule in migrated.url_map.iter_rules():
        if rule.rule.startswith("/p/"):
            assert rule.methods - {"HEAD", "OPTIONS"} == {"GET"}


def test_class_public_needs_admin_and_a_valid_action(make_client, migrated):
    _solved()
    a, _ = _two_classes()
    assert (
        make_client("viewer")
        .post(f"/data/classes/{a.id}/public", data={"action": "on"})
        .status_code
        == 403
    )
    assert (
        make_client("admin")
        .post(f"/data/classes/{a.id}/public", data={"action": "x"})
        .status_code
        == 400
    )


@pytest.mark.parametrize("role,code", [("viewer", 403), (None, 302)])
def test_qr_page_is_admin_only(make_client, migrated, role, code):
    assert make_client(role).get("/data/classes/qr").status_code == code


def test_qr_page_has_one_card_per_enabled_class(make_client, migrated):
    _solved()
    admin = make_client("admin")
    a, b = _two_classes()
    assert "Няма клас с включена" in admin.get("/data/classes/qr").text
    token = _enable(admin, a.id)
    html = admin.get("/data/classes/qr").text
    assert html.count("<svg") == 1 and a.name in html
    assert f"http://localhost/p/{token}" in html  # absolute URL, shown as text
    assert html.count('class="col-12 col-md-6 col-lg-4 qr-sheet"') == 1
    assert f"QR код за {b.name}" not in html
