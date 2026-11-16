"""FE-15: manual moves, the checker before saving, pins."""

from flask import current_app
from sqlalchemy import select

from app import models as m
from app.history import how_made
from app.models import db
from app.routes.solve import _edited
from tests.app.test_history import _solved


def _lessons(job_id):
    return db.session.scalars(
        select(m.Lesson).where(m.Lesson.job_id == job_id).order_by(m.Lesson.id)
    ).all()


def _newest():
    return db.session.scalar(select(m.SolveJob).order_by(m.SolveJob.id.desc()))


def _move(client, les, day, start, **extra):
    data = {"day": day, "start_period": start, **extra}
    return client.post(f"/lessons/{les.id}/move", data=data)


def _next_day(les):
    return les.day % current_app.config["SCHOOL_DAYS"] + 1


def test_clean_move_saves_a_new_version_and_keeps_the_old(make_client, migrated):
    first = _solved()
    admin = make_client("admin")
    before = [(x.day, x.start_period, x.room_id) for x in _lessons(first)]
    saved = None
    for les in _lessons(first):  # some move is conflict-free in a small school
        for day in range(1, current_app.config["SCHOOL_DAYS"] + 1):
            for period in db.session.scalars(select(m.Period.number)):
                r = _move(admin, les, day, period)
                if r.status_code == 302 and (day, period) != (
                    les.day,
                    les.start_period,
                ):
                    saved = les, day, period
                    break
            if saved:
                break
        if saved:
            break
    assert saved, "no conflict-free move found"
    les, day, period = saved
    new = _newest()
    assert new.id != first and new.base_job_id == first and new.created_by_id
    assert how_made(new, _edited(new))[0] == "edited"
    assert [(x.day, x.start_period, x.room_id) for x in _lessons(first)] == before
    moved = [x for x in _lessons(new.id) if (x.day, x.start_period) == (day, period)]
    assert moved
    assert "Преместени: 1" in admin.get(f"/history/{new.id}").text


def test_move_into_a_conflict_shows_the_violation_then_saves_on_confirm(
    make_client, migrated
):
    first = _solved()
    admin = make_client("admin")
    les = _lessons(first)[0]
    admin.post(f"/lessons/{les.id}/pin")  # a pinned course moved away breaks fixed_slot
    n = db.session.scalar(select(db.func.count()).select_from(m.SolveJob))
    r = _move(admin, les, _next_day(les), les.start_period)
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "Запази въпреки това" in page
    assert "Блокът не е във фиксирания си час." in page
    assert db.session.scalar(select(db.func.count()).select_from(m.SolveJob)) == n
    r = _move(admin, les, _next_day(les), les.start_period, confirm=1)
    assert r.status_code == 302
    new = _newest()
    assert new.id != first
    kept = db.session.scalars(
        select(m.Violation).where(m.Violation.job_id == new.id, m.Violation.hard)
    ).all()
    assert any(v.message_key == "block_not_at_fixed_slot" for v in kept)


def test_pin_creates_updates_and_removes_a_fixed_slot(make_client, migrated):
    first = _solved()
    admin = make_client("admin")
    les = _lessons(first)[0]
    admin.post(f"/lessons/{les.id}/pin")
    admin.post(f"/lessons/{les.id}/pin")  # no duplicate
    pins = db.session.scalars(select(m.Constraint)).all()
    assert len(pins) == 1
    c = pins[0]
    assert (c.type, c.status, c.source, c.hard) == (
        "fixed_slot",
        "confirmed",
        "manual",
        True,
    )
    assert c.params["course_id"] == les.course_id
    assert {"day": les.day, "start_period": les.start_period} in c.params["blocks"]
    assert "закачен" in admin.get(f"/timetable?job={first}").text
    admin.post(f"/lessons/{les.id}/unpin")
    assert db.session.scalars(select(m.Constraint)).all() == []


def test_bad_target_is_refused(make_client, migrated):
    first = _solved()
    les = _lessons(first)[0]
    admin = make_client("admin")
    assert _move(admin, les, 99, 1).status_code == 302
    assert _newest().id == first
    assert "Премести" in admin.get(f"/timetable?job={first}").text
    assert "Начален час" in admin.get(f"/lessons/{les.id}/move").text


def test_viewer_gets_403_and_csrf_is_required(make_client, migrated):
    first = _solved()
    les = _lessons(first)[0]
    viewer = make_client("viewer")
    for method, url in (
        ("POST", f"/lessons/{les.id}/move"),
        ("GET", f"/lessons/{les.id}/move"),
        ("POST", f"/lessons/{les.id}/pin"),
        ("POST", f"/lessons/{les.id}/unpin"),
    ):
        assert viewer.open(url, method=method).status_code == 403, url
    assert "Премести" not in viewer.get(f"/timetable?job={first}").text
    admin = make_client("admin")
    migrated.config["WTF_CSRF_ENABLED"] = True
    assert _move(admin, les, 1, 1).status_code == 400
    assert admin.post(f"/lessons/{les.id}/pin").status_code == 400
