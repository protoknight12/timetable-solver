"""One click from "impossible" to a timetable: relax the conflicting hard rules."""

import pytest
from sqlalchemy import func, select

from app import models as m
from app.jobs import run_solve
from app.models import db
from app.routes.solve import RELAXED_WEIGHT
from tests.app.test_solve_jobs import (
    _add_constraint,
    _all_slots_of_first_teacher,
    _job,
    _load,
)


@pytest.fixture
def queued(migrated):
    calls = []
    migrated.config["SOLVE_ENQUEUE"] = lambda job_id, timeout: calls.append(job_id)
    return calls


def _infeasible_job() -> tuple[int, m.Constraint, m.Constraint]:
    """Tiny school, one teacher never available (culprit) plus a harmless rule."""
    _load("tiny_school.json")
    _add_constraint(
        "confirmed", "teacher_unavailable_slots", _all_slots_of_first_teacher()
    )
    _add_constraint(
        "confirmed", "max_lessons_per_day", {"scope": {"class_id": 1}, "max": 5}
    )
    culprit, harmless = db.session.scalars(
        select(m.Constraint).order_by(m.Constraint.id)
    ).all()
    job_id = _job()
    run_solve(job_id)
    job = db.session.get(m.SolveJob, job_id)
    assert job.solver_status == "INFEASIBLE"
    return job_id, culprit, harmless


def _jobs() -> int:
    return db.session.scalar(select(func.count()).select_from(m.SolveJob))


def test_relax_changes_exactly_the_conflict_set_and_starts_a_job(make_client, queued):
    job_id, culprit, harmless = _infeasible_job()
    r = make_client("admin").post(f"/jobs/{job_id}/relax", follow_redirects=True)
    assert "1 правило стана предпочитание. Изграждането започна." in r.get_data(True)
    db.session.refresh(culprit)
    db.session.refresh(harmless)
    assert culprit.hard is False and culprit.weight == RELAXED_WEIGHT
    assert harmless.hard is True and harmless.weight is None
    assert len(queued) == 1 and queued[0] != job_id


def test_relax_ignores_ids_from_the_request_body(make_client, queued):
    job_id, _, harmless = _infeasible_job()
    make_client("admin").post(
        f"/jobs/{job_id}/relax", data={"ids": str(harmless.id), "constraint_id": "2"}
    )
    db.session.refresh(harmless)
    assert harmless.hard is True


def test_relax_keeps_an_existing_weight(make_client, queued):
    job_id, culprit, _ = _infeasible_job()
    culprit.weight = 9  # leftover from an earlier soft setting
    db.session.commit()
    make_client("admin").post(f"/jobs/{job_id}/relax")
    db.session.refresh(culprit)
    assert culprit.hard is False and culprit.weight == 9


def test_relax_skips_soft_deleted_and_unconfirmed(make_client, queued):
    job_id, culprit, _ = _infeasible_job()
    culprit.hard, culprit.weight = False, 3  # already soft
    db.session.commit()
    admin = make_client("admin")
    r = admin.post(f"/jobs/{job_id}/relax", follow_redirects=True)
    assert "Няма твърди правила" in r.get_data(True)
    assert queued == [] and _jobs() == 1
    db.session.delete(culprit)  # deleted
    db.session.commit()
    admin.post(f"/jobs/{job_id}/relax")
    assert queued == [] and _jobs() == 1


def test_relax_skips_a_rule_that_is_no_longer_confirmed(make_client, queued):
    job_id, culprit, _ = _infeasible_job()
    culprit.status = "rejected"
    db.session.commit()
    make_client("admin").post(f"/jobs/{job_id}/relax")
    db.session.refresh(culprit)
    assert culprit.hard is True and queued == []


def test_relax_refuses_a_job_with_a_timetable(make_client, queued, migrated):
    _load("tiny_school.json")
    job_id = _job()
    run_solve(job_id)
    r = make_client("admin").post(f"/jobs/{job_id}/relax")
    assert r.status_code == 404 and queued == []


def test_relax_refuses_infeasible_job_without_conflict_ids(make_client, queued):
    _load("impossible_school.json")
    job_id = _job()
    run_solve(job_id)
    admin = make_client("admin")
    assert admin.post(f"/jobs/{job_id}/relax").status_code == 404
    assert admin.post("/jobs/9999/relax").status_code == 404
    assert queued == []


def test_relax_while_a_job_runs_changes_nothing(make_client, queued):
    job_id, culprit, _ = _infeasible_job()
    db.session.add(m.SolveJob(status="running", time_limit_s=60))
    db.session.commit()
    r = make_client("admin").post(f"/jobs/{job_id}/relax", follow_redirects=True)
    assert "Вече се изгражда график" in r.get_data(True)
    db.session.refresh(culprit)
    assert culprit.hard is True and queued == []


def test_viewer_cannot_relax(make_client, queued):
    job_id, culprit, _ = _infeasible_job()
    assert make_client("viewer").post(f"/jobs/{job_id}/relax").status_code == 403
    db.session.refresh(culprit)
    assert culprit.hard is True and queued == []


def test_relax_without_csrf_is_rejected(make_client, migrated, queued):
    job_id, culprit, _ = _infeasible_job()
    client = make_client("admin")
    migrated.config["WTF_CSRF_ENABLED"] = True
    assert client.post(f"/jobs/{job_id}/relax").status_code == 400
    db.session.refresh(culprit)
    assert culprit.hard is True and queued == []


def test_button_is_shown_to_admin_only(make_client, queued):
    job_id, _, _ = _infeasible_job()
    admin = make_client("admin").get("/timetable").get_data(True)
    assert f"/jobs/{job_id}/relax" in admin and "изгради отново" in admin
    viewer = make_client("viewer").get(f"/timetable?job={job_id}").get_data(True)
    assert "/relax" not in viewer


def test_end_to_end_infeasible_relax_then_soft_violations(make_client, queued):
    job_id, culprit, _ = _infeasible_job()
    admin = make_client("admin")
    admin.post(f"/jobs/{job_id}/relax")
    new_id = queued[0]
    run_solve(new_id)  # what the worker would do
    job = db.session.get(m.SolveJob, new_id)
    assert job.status == "finished" and job.solver_status in ("OPTIMAL", "FEASIBLE")
    rows = db.session.scalars(
        select(m.Violation).where(m.Violation.job_id == new_id)
    ).all()
    assert [(v.type, v.message_key) for v in rows if v.hard] == []
    assert any(v.constraint_id == culprit.id and not v.hard for v in rows)
    # The rule list shows it as a preference.
    html = admin.get("/constraints/").get_data(True)
    assert f"Предпочитание (тежест {RELAXED_WEIGHT})" in html
