"""Read-only REST API under /api/v1."""

import re

from sqlalchemy import select

from app import models as m
from app.api_spec import spec
from app.models import db
from tests.app.test_public_page import _enable
from tests.app.test_timetable_page import _solved

TOKEN = "test-api-token-0123456789"


def _published(make_client):
    job_id = _solved()
    admin = make_client("admin")
    assert admin.post(f"/jobs/{job_id}/publish").status_code == 302
    return job_id, admin


def test_anonymous_gets_json_401_not_a_login_redirect(migrated):
    r = migrated.test_client().get("/api/v1/timetable")
    assert r.status_code == 401 and r.is_json
    assert r.json["error"]["code"] == 401
    assert r.headers["WWW-Authenticate"].startswith("Bearer")


def test_index_and_openapi_need_no_login(migrated):
    client = migrated.test_client()
    assert client.get("/api/v1").json["read_only"] is True
    assert client.get("/api/v1/openapi.json").json["openapi"] == "3.1.0"


def test_viewer_reads_the_published_timetable(make_client, migrated):
    job_id, _ = _published(make_client)
    viewer = make_client("viewer")
    r = viewer.get("/api/v1/timetable")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    data = r.json
    assert data["timetable"]["job_id"] == job_id
    assert data["days"][0] == {"number": 1, "name": "Понеделник"}
    assert len(data["lessons"]) == db.session.scalar(
        select(db.func.count()).select_from(m.Lesson).where(m.Lesson.job_id == job_id)
    )
    lesson = data["lessons"][0]
    assert set(lesson) >= {
        "day",
        "start_period",
        "length",
        "subject",
        "teachers",
        "groups",
    }


def test_draft_is_never_returned(make_client, migrated):
    _solved()  # finished, not published
    r = make_client("admin").get("/api/v1/timetable")
    assert r.status_code == 404 and "публикувано" in r.json["error"]["message"]


def test_filters_match_the_class_and_teacher_views(make_client, migrated):
    _published(make_client)
    viewer = make_client("viewer")
    cls = db.session.scalars(select(m.SchoolClass)).first()
    by_class = viewer.get(f"/api/v1/classes/{cls.id}/timetable").json
    assert by_class["class"]["name"] == cls.name and by_class["lessons"]
    for lesson in by_class["lessons"]:
        assert {g["class_id"] for g in lesson["groups"]} == {cls.id}
    same = viewer.get(f"/api/v1/timetable?class_id={cls.id}").json["lessons"]
    assert [x["id"] for x in same] == [x["id"] for x in by_class["lessons"]]

    teacher_id = by_class["lessons"][0]["teachers"][0]["id"]
    by_teacher = viewer.get(f"/api/v1/teachers/{teacher_id}/timetable").json
    assert all(
        teacher_id in {t["id"] for t in x["teachers"]} for x in by_teacher["lessons"]
    )
    monday = viewer.get("/api/v1/timetable?day=1").json["lessons"]
    assert monday and {x["day"] for x in monday} == {1}


def test_bad_ids_give_json_404(make_client, migrated):
    _published(make_client)
    viewer = make_client("viewer")
    for url in (
        "/api/v1/classes/999999/timetable",
        "/api/v1/teachers/999999/timetable",
        "/api/v1/timetable?class_id=abc",
        "/api/v1/no-such-thing",
    ):
        r = viewer.get(url)
        assert r.status_code == 404 and r.is_json, url


def test_lists(make_client, migrated):
    _published(make_client)
    viewer = make_client("viewer")
    for key in ("classes", "teachers", "rooms", "subjects"):
        r = viewer.get(f"/api/v1/{key}")
        assert r.status_code == 200 and isinstance(r.json[key], list), key
    periods = viewer.get("/api/v1/periods").json["periods"]
    assert periods and re.fullmatch(r"\d\d:\d\d", periods[0]["start"])


def test_bearer_token(make_client, migrated):
    _published(make_client)
    client = migrated.test_client()
    auth = {"Authorization": f"Bearer {TOKEN}"}
    # No API_TOKEN configured: bearer access is off.
    assert client.get("/api/v1/timetable", headers=auth).status_code == 401
    migrated.config["API_TOKEN"] = TOKEN
    assert client.get("/api/v1/timetable", headers=auth).status_code == 200
    wrong = {"Authorization": "Bearer wrong"}
    assert client.get("/api/v1/timetable", headers=wrong).status_code == 401


def test_public_token_gives_one_class_only(make_client, migrated):
    _, admin = _published(make_client)
    a, _ = db.session.scalars(select(m.SchoolClass).order_by(m.SchoolClass.id)).all()[
        :2
    ]
    token = _enable(admin, a.id)
    anon = migrated.test_client()
    data = anon.get(f"/api/v1/public/{token}").json
    assert data["class"]["id"] == a.id and data["lessons"]
    assert all(g["class_id"] == a.id for x in data["lessons"] for g in x["groups"])
    assert anon.get("/api/v1/public/no-such-token").status_code == 404


def test_api_is_read_only(make_client, migrated):
    _, admin = _published(make_client)
    r = admin.post("/api/v1/timetable")
    assert r.status_code == 405 and r.is_json
    for rule in migrated.url_map.iter_rules():
        if rule.rule.startswith("/api/"):
            assert rule.methods <= {"GET", "HEAD", "OPTIONS"}, rule.rule


def test_api_is_rate_limited(make_client, migrated):
    migrated.config["API_RATE_LIMIT"] = "3 per minute"
    viewer = make_client("viewer")
    codes = [viewer.get("/api/v1/classes").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    assert viewer.get("/api/v1/classes").is_json


def test_openapi_lists_exactly_the_api_routes(migrated):
    routes = set()
    for rule in migrated.url_map.iter_rules():
        if rule.rule.startswith("/api/v1"):
            path = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", rule.rule[len("/api/v1") :])
            routes.add(path or "/")
    assert routes == set(spec()["paths"])
