"""Start a solve job and report its progress (BE-08). No pages: the UI is FE-08."""

from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, url_for
from flask_login import current_user, login_required
from redis import Redis
from rq import Queue
from sqlalchemy import DateTime, func, select, type_coerce

from app.auth import admin_required, limiter
from app.jobs import run_solve
from app.models import Lesson, SolveJob, Violation, db

bp = Blueprint("solve", __name__)

# Extra seconds on top of the solver limit for loading, checking and saving.
TIMEOUT_MARGIN_S = 60


def _enqueue(job_id: int, timeout_s: int) -> None:
    enqueue = current_app.config["SOLVE_ENQUEUE"]
    if enqueue:
        enqueue(job_id, timeout_s)
        return
    queue = Queue(connection=Redis.from_url(current_app.config["REDIS_URL"]))
    queue.enqueue(run_solve, job_id, job_timeout=timeout_s)


@bp.post("/solve")
@admin_required
@limiter.limit(
    lambda: current_app.config["SOLVE_RATE_LIMIT"],
    key_func=lambda: str(current_user.get_id()),
)
def start():
    # A job still queued/running well past its RQ timeout lost its worker: fail
    # it so it cannot block new solves. Ages use the database clock, the same
    # one that wrote created_at. shortcut: check-then-insert can still race;
    # admin-only plus the rate limit make that unlikely.
    db_now = db.session.scalar(select(type_coerce(func.now(), DateTime)))
    db_now = db_now.replace(tzinfo=None)  # PostgreSQL returns an aware value
    active = db.session.scalars(
        select(SolveJob).where(SolveJob.status.in_(("queued", "running")))
    ).all()
    fresh = False
    for stale in active:
        limit = stale.time_limit_s or current_app.config["SOLVER_TIME_LIMIT_S"]
        if (db_now - stale.created_at).total_seconds() > limit + TIMEOUT_MARGIN_S + 60:
            stale.status, stale.finished_at = "failed", func.now()
            stale.error = "Изчислението беше прекъснато. Стартирайте го отново."
        else:
            fresh = True
    db.session.commit()
    if fresh:
        flash("Вече се изгражда график. Изчакайте края на изчислението.", "warning")
        return redirect(url_for("pages.index"))
    limit = current_app.config["SOLVER_TIME_LIMIT_S"]
    job = SolveJob(status="queued", time_limit_s=limit)
    db.session.add(job)
    db.session.commit()
    try:
        _enqueue(job.id, limit + TIMEOUT_MARGIN_S)
    except Exception:
        current_app.logger.exception("could not enqueue solve job %s", job.id)
        job.status, job.finished_at = "failed", func.now()
        job.error = "Изчислението не може да бъде стартирано. Опитайте отново."
        db.session.commit()
        flash(job.error, "danger")
    else:
        flash("Изграждането на графика започна.", "success")
    return redirect(url_for("pages.index"))


@bp.get("/jobs/<int:job_id>/status")
@login_required
def status(job_id: int):
    job = db.session.get(SolveJob, job_id) or abort(404)

    def count(model, *where):
        return db.session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.job_id == job_id, *where)
        )

    return jsonify(
        status=job.status,
        solver_status=job.solver_status,
        objective=job.objective,
        error=job.error,
        lesson_count=count(Lesson),
        hard_violations=count(Violation, Violation.hard.is_(True)),
        soft_violations=count(Violation, Violation.hard.is_(False)),
    )
