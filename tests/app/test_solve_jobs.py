import pytest
from sqlalchemy import func, select

from app import jobs
from app import models as m
from app.cli import load_school
from app.jobs import run_solve
from app.models import db
from tools.seed_to_contract import SEED, load_seed


def _load(name: str) -> None:
    load_school(load_seed(SEED.parent / name))
    db.session.commit()


def _job() -> int:
    job = m.SolveJob(status="queued", time_limit_s=20)
    db.session.add(job)
    db.session.commit()
    return job.id


def _count(model, job_id):
    return db.session.scalar(
        select(func.count()).select_from(model).where(model.job_id == job_id)
    )


def _add_constraint(status, type_, params, hard=True):
    db.session.add(m.Constraint(type=type_, params=params, hard=hard, status=status))
    db.session.commit()


def _all_slots_of_first_teacher():
    from app.solver_input import build_solver_input

    inp = build_solver_input(db.session)
    teacher = next(t for c in inp.courses for p in c.parts for t in p.teacher_ids)
    slots = [{"day": d, "period": p} for d in inp.days for p in inp.periods]
    return {"teacher_id": teacher, "slots": slots}


def test_tiny_seed_is_solved_and_saved(migrated):
    _load("tiny_school.json")
    job_id = _job()
    run_solve(job_id)
    job = db.session.get(m.SolveJob, job_id)
    assert job.status == "finished" and job.error is None
    assert job.solver_status in ("OPTIMAL", "FEASIBLE")
    assert job.started_at and job.finished_at
    assert _count(m.Lesson, job_id) > 0
    hard = db.session.scalar(
        select(func.count()).where(m.Violation.job_id == job_id, m.Violation.hard)
    )
    assert hard == 0


def test_impossible_seed_finishes_without_lessons(migrated):
    _load("impossible_school.json")
    job_id = _job()
    run_solve(job_id)
    job = db.session.get(m.SolveJob, job_id)
    assert job.status == "finished" and job.solver_status == "INFEASIBLE"
    assert _count(m.Lesson, job_id) == 0
    # The checker reports "no timetable" as one hard violation.
    assert _count(m.Violation, job_id) == 1


def test_constraint_on_deleted_teacher_fails_without_solving(migrated, monkeypatch):
    _load("tiny_school.json")
    _add_constraint(
        "confirmed", "teacher_available_days", {"teacher_id": 9999, "days": [1]}
    )

    def boom(*a, **k):
        raise AssertionError("solve must not be called")

    monkeypatch.setattr(jobs, "solve", boom)
    job_id = _job()
    run_solve(job_id)
    job = db.session.get(m.SolveJob, job_id)
    assert job.status == "failed" and job.error and job.finished_at
    assert "Прегледайте" in job.error
    assert _count(m.Lesson, job_id) == 0


def test_only_confirmed_constraints_are_used(migrated):
    _load("tiny_school.json")
    _add_constraint(
        "proposed", "teacher_unavailable_slots", _all_slots_of_first_teacher()
    )
    job_id = _job()
    run_solve(job_id)
    job = db.session.get(m.SolveJob, job_id)
    assert job.status == "finished" and job.solver_status in ("OPTIMAL", "FEASIBLE")
    # Control: the same constraint, confirmed, makes the problem infeasible.
    db.session.query(m.Constraint).update({"status": "confirmed"})
    db.session.commit()
    second = _job()
    run_solve(second)
    assert db.session.get(m.SolveJob, second).solver_status == "INFEASIBLE"


def test_solver_exception_marks_the_job_failed(migrated, monkeypatch):
    _load("tiny_school.json")

    def boom(*a, **k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(jobs, "solve", boom)
    job_id = _job()
    run_solve(job_id)
    job = db.session.get(m.SolveJob, job_id)
    assert job.status == "failed" and job.finished_at
    assert job.error == jobs.ERROR_UNEXPECTED  # no internals shown to users
    assert _count(m.Lesson, job_id) == 0


# --- routes ---------------------------------------------------------------


@pytest.fixture
def queued(migrated):
    calls = []
    migrated.config["SOLVE_ENQUEUE"] = lambda job_id, timeout: calls.append(
        (job_id, timeout)
    )
    return calls


def test_viewer_cannot_start_a_solve(make_client, queued):
    assert make_client("viewer").post("/solve").status_code == 403
    assert queued == []


def test_post_without_csrf_is_rejected(make_client, migrated, queued):
    client = make_client("admin")
    migrated.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/solve").status_code == 400
    assert queued == []


def test_admin_starts_a_job_and_a_second_is_refused(make_client, migrated, queued):
    client = make_client("admin")
    assert client.post("/solve").status_code == 302
    assert len(queued) == 1
    job_id, timeout = queued[0]
    job = db.session.get(m.SolveJob, job_id)
    assert job.status == "queued" and timeout > job.time_limit_s
    for status in ("queued", "running"):
        job.status = status
        db.session.commit()
        r = client.post("/solve", follow_redirects=True)
        assert "Вече се изгражда график" in r.get_data(as_text=True)
    assert len(queued) == 1
    assert db.session.scalar(select(func.count()).select_from(m.SolveJob)) == 1


def test_enqueue_failure_fails_the_job(make_client, migrated):
    def down(*a):
        raise ConnectionError("redis is down")

    migrated.config["SOLVE_ENQUEUE"] = down
    make_client("admin").post("/solve")
    job = db.session.scalars(select(m.SolveJob)).one()
    assert job.status == "failed" and job.error


def test_solve_is_rate_limited(make_client, migrated, queued):
    migrated.config["SOLVE_RATE_LIMIT"] = "2 per minute"
    client = make_client("admin")
    codes = [client.post("/solve").status_code for _ in range(3)]
    assert codes == [302, 302, 429]


def test_status_json_of_a_finished_job(make_client, migrated):
    _load("tiny_school.json")
    job_id = _job()
    run_solve(job_id)
    body = make_client("viewer").get(f"/jobs/{job_id}/status").get_json()
    assert body["status"] == "finished" and body["error"] is None
    assert body["solver_status"] in ("OPTIMAL", "FEASIBLE")
    assert body["lesson_count"] == _count(m.Lesson, job_id) > 0
    assert body["hard_violations"] == 0 and body["soft_violations"] == 0
    assert set(body) == {
        "status",
        "solver_status",
        "objective",
        "error",
        "lesson_count",
        "hard_violations",
        "soft_violations",
    }


def test_status_of_unknown_job_is_404_and_needs_login(make_client, migrated):
    assert make_client("viewer").get("/jobs/999/status").status_code == 404
    assert migrated.test_client().get("/jobs/1/status").status_code == 302
