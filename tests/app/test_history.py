"""BE-21: publish, history and what changed."""

from sqlalchemy import select

from app import models as m
from app.history import diff_lessons, how_made
from app.jobs import run_solve
from app.models import db
from schema.contract import LessonPlacement as L
from tests.app.test_solve_jobs import _job, _load


def test_diff_of_two_known_versions():
    old = [
        L(1, 10, 1, 1, 1, 5),
        L(1, 11, 2, 1, 1, 5),
        L(2, 20, 3, 2, 2, 6),
        L(2, 21, 4, 1, 1),
    ]
    new = [
        L(1, 10, 1, 1, 1, 5),  # same: not reported
        L(1, 11, 2, 1, 1, 7),  # only the room changed
        L(2, 20, 5, 3, 2, 6),  # moved
        L(3, 30, 1, 1, 1),  # added (21 was removed)
    ]
    got = {
        (d["kind"], (d["before"] or d["after"]).part_id) for d in diff_lessons(old, new)
    }
    assert got == {("room", 11), ("moved", 20), ("removed", 21), ("added", 30)}
    assert diff_lessons(old, old) == []


def _solved():
    _load("tiny_school.json")
    job_id = _job()
    run_solve(job_id)
    return job_id


def test_publish_switches_and_viewers_see_the_published(make_client, migrated):
    first = _solved()
    admin, viewer = make_client("admin"), make_client("viewer")
    admin.post(f"/jobs/{first}/restore")
    second = db.session.scalar(select(m.SolveJob).order_by(m.SolveJob.id.desc())).id
    assert second != first
    # Nothing published: the viewer sees the newest feasible version.
    assert f"Изчисление №{second}" in viewer.get("/timetable").text
    assert admin.post(f"/jobs/{first}/publish").status_code == 302
    assert (
        viewer.get(f"/timetable?job={second}").text.count(f"Изчисление №{first}") == 1
    )
    assert "Показва се чернова" in admin.get("/timetable").text
    assert "Показва се чернова" not in viewer.get("/timetable").text
    admin.post(f"/jobs/{second}/publish")
    db.session.expire_all()
    published = db.session.scalars(
        select(m.SolveJob.id).where(m.SolveJob.published_at.isnot(None))
    ).all()
    assert published == [second]


def test_restore_creates_a_new_job_and_diff_shows_the_move(make_client, migrated):
    first = _solved()
    admin = make_client("admin")
    admin.post(f"/jobs/{first}/restore")
    new = db.session.scalar(select(m.SolveJob).order_by(m.SolveJob.id.desc()))
    assert new.id != first and new.status == "finished" and new.base_job_id == first
    assert how_made(new)[0] == "copy" and new.created_by_id is not None
    n = lambda j: db.session.scalar(
        select(db.func.count()).select_from(m.Lesson).where(m.Lesson.job_id == j)
    )
    assert n(new.id) == n(first)
    assert "Няма промени" in admin.get(f"/history/{new.id}").text
    # Move one lesson in the copy: the diff and the grid must show it.
    les = db.session.scalars(select(m.Lesson).where(m.Lesson.job_id == new.id)).first()
    part = db.session.get(m.CoursePart, les.part_id)
    les.day = les.day % 5 + 1
    db.session.commit()
    page = admin.get(f"/history/{new.id}").text
    assert "Преместени: 1" in page and "Преместен" in page
    class_id = part.groups[0].class_id
    assert "променен" in admin.get(f"/timetable?job={new.id}&class={class_id}").text
    assert "Преместени: 0" in admin.get(f"/history/{new.id}?teacher=999999").text
    assert "№" in admin.get("/history").text


def test_viewer_cannot_publish_restore_or_open_history(make_client, migrated):
    first = _solved()
    viewer = make_client("viewer")
    for method, url in (
        ("POST", f"/jobs/{first}/publish"),
        ("POST", f"/jobs/{first}/restore"),
        ("GET", "/history"),
        ("GET", f"/history/{first}"),
    ):
        assert viewer.open(url, method=method).status_code == 403, url
    assert db.session.get(m.SolveJob, first).published_at is None


def test_publish_refuses_a_job_without_timetable(make_client, migrated):
    bad = _job()
    assert make_client("admin").post(f"/jobs/{bad}/publish").status_code == 404
