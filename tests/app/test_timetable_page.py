from sqlalchemy import select

from app import models as m
from app.jobs import run_solve
from app.models import db
from tests.app.test_solve_jobs import _job, _load


def _solved(seed="tiny_school.json"):
    _load(seed)
    job_id = _job()
    run_solve(job_id)
    return job_id


def test_both_roles_see_grid_and_report(make_client, migrated):
    _solved()
    for role, button in (("admin", True), ("viewer", False)):
        html = make_client(role).get("/timetable").get_data(as_text=True)
        assert "Понеделник" in html and "Твърди нарушения (0)" in html
        assert ("Оптимално решение" in html) or ("Допустимо решение" in html)
        assert ("Изгради разписание" in html) is button


def test_anonymous_is_sent_to_login(migrated):
    r = migrated.test_client().get("/timetable")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_class_picker_changes_the_grid(make_client, migrated):
    _solved()
    client = make_client("viewer")
    ids = [c.id for c in db.session.scalars(select(m.SchoolClass))]
    pages = {i: client.get(f"/timetable?class={i}").text for i in ids}
    assert len(set(pages.values())) == len(ids)


def test_split_group_lessons_are_both_in_the_cell(make_client, migrated):
    job_id = _solved("demo_school.json")
    # Two parts of a split course share one slot; both must show up for the class.
    from app.routes.solve import _grid

    found = False

    for c in db.session.scalars(select(m.SchoolClass)):
        for entries in _grid(job_id, c.id).values():
            if len(entries) > 1:
                assert all(e["group"] for e in entries)
                found = True
    assert found


def test_infeasible_shows_plain_message(make_client, migrated):
    _load("impossible_school.json")
    run_solve(_job())
    html = make_client("viewer").get("/timetable").get_data(as_text=True)
    assert "Няма решение" in html and "infeasible-explanation" in html
    assert "<table" not in html


def test_job_card_polls_until_done(make_client, migrated):
    job = m.SolveJob(status="running", time_limit_s=20)
    db.session.add(job)
    db.session.commit()
    client = make_client("viewer")
    r = client.get(f"/jobs/{job.id}/card")
    assert "hx-trigger" in r.text and "HX-Refresh" not in r.headers
    job.status = "failed"
    job.error = "Грешка"
    db.session.commit()
    r = client.get(f"/jobs/{job.id}/card")
    assert "hx-trigger" not in r.text and r.headers["HX-Refresh"] == "true"
