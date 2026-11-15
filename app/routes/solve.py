"""Start a solve job, report its progress (BE-08) and show the result (FE-08)."""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from redis import Redis
from rq import Queue
from sqlalchemy import DateTime, func, select, type_coerce
from sqlalchemy.orm import selectinload

from app.auth import admin_required, limiter
from app.constraint_forms import DAY_NAMES
from app.jobs import run_solve
from app.models import (
    CoursePart,
    Lesson,
    Period,
    Room,
    SchoolClass,
    SolveJob,
    Teacher,
    Violation,
    db,
)
from app.solve_text import FALLBACK_MESSAGE, JOB_STATUS, MESSAGES, SOLVER_STATUS

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
        return redirect(url_for("solve.timetable"))
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
    return redirect(url_for("solve.timetable"))


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


ACTIVE = ("queued", "running")
# Only these solver statuses carry a timetable (same rule as checker A1).
HAS_TIMETABLE = ("OPTIMAL", "FEASIBLE")


def _entity_names() -> dict[str, dict[int, str]]:
    def names(model, attr="name"):
        return {r.id: getattr(r, attr) for r in db.session.scalars(select(model))}

    return {
        "teacher": names(Teacher),
        "class": names(SchoolClass),
        "room": names(Room),
        "day": {d: n.capitalize() for d, n in DAY_NAMES.items()},
    }


def _violation_rows(job_id: int) -> list[dict]:
    names = _entity_names()
    kinds = {
        "teacher": "Учител",
        "class": "Клас",
        "room": "Стая",
        "day": "Ден",
        "period": "Час",
        "course": "Учебен план №",
        "part": "Част №",
        "block": "Блок №",
    }
    rows = []
    for v in db.session.scalars(
        select(Violation).where(Violation.job_id == job_id).order_by(Violation.id)
    ):
        about = []
        for kind, ident in v.entity_ids:
            value = names.get(kind, {}).get(ident, ident + (kind == "block"))
            sep = "" if kind in ("course", "part", "block") else ": "
            about.append(f"{kinds.get(kind, kind)}{sep}{value}")
        rows.append(
            {
                "hard": v.hard,
                "text": MESSAGES.get(v.message_key, FALLBACK_MESSAGE),
                "about": about,
                "constraint_id": v.constraint_id,
            }
        )
    return rows


def _grid(job_id: int, class_id: int) -> dict[tuple[int, int], list[dict]]:
    """(day, period) -> lessons of this class; a group-split slot has several."""
    parts = db.session.scalars(
        select(CoursePart).options(
            selectinload(CoursePart.groups),
            selectinload(CoursePart.teachers),
            selectinload(CoursePart.subject),
        )
    )
    parts = {p.id: p for p in parts}
    rooms = _entity_names()["room"]
    cells: dict[tuple[int, int], list[dict]] = {}
    for les in db.session.scalars(select(Lesson).where(Lesson.job_id == job_id)):
        part = parts[les.part_id]
        mine = [g for g in part.groups if g.class_id == class_id]
        if not mine:
            continue
        entry = {
            "subject": part.subject.name,
            "teachers": ", ".join(sorted(t.name for t in part.teachers)),
            "room": rooms.get(les.room_id, ""),
            # The whole class needs no group label.
            "group": ""
            if any(g.is_whole for g in mine)
            else ", ".join(sorted(g.name for g in mine)),
        }
        for period in range(les.start_period, les.start_period + les.length):
            cells.setdefault((les.day, period), []).append(entry)
    return cells


@bp.get("/timetable")
@login_required
def timetable():
    job = db.session.get(SolveJob, request.args.get("job", type=int) or 0)
    if job is None:  # shortcut: no job picker, the newest job is shown
        job = db.session.scalar(select(SolveJob).order_by(SolveJob.id.desc()))
    classes = db.session.scalars(select(SchoolClass).order_by(SchoolClass.id)).all()
    class_id = request.args.get("class", type=int)
    current = next((c for c in classes if c.id == class_id), None) or (
        classes[0] if classes else None
    )
    ctx = {"job": job, "classes": classes, "current": current, "active": "timetable"}
    if job and job.status == "finished" and job.solver_status in HAS_TIMETABLE:
        ctx |= {
            "cells": _grid(job.id, current.id) if current else {},
            "days": range(1, current_app.config["SCHOOL_DAYS"] + 1),
            "periods": db.session.scalars(select(Period).order_by(Period.number)).all(),
            "violations": _violation_rows(job.id),
        }
    elif job and job.status == "finished":
        ctx["violations"] = _violation_rows(job.id)
    return render_template(
        "solve/timetable.html",
        job_status=JOB_STATUS,
        solver_status=SOLVER_STATUS,
        day_names={d: n.capitalize() for d, n in DAY_NAMES.items()},
        active_states=ACTIVE,
        **ctx,
    )


@bp.get("/jobs/<int:job_id>/card")
@login_required
def job_card(job_id: int):
    """HTMX polling target. When the job ends the whole page reloads to show it."""
    job = db.session.get(SolveJob, job_id) or abort(404)
    resp = current_app.make_response(
        render_template(
            "solve/_job.html",
            job=job,
            job_status=JOB_STATUS,
            solver_status=SOLVER_STATUS,
            active_states=ACTIVE,
        )
    )
    if job.status not in ACTIVE:
        resp.headers["HX-Refresh"] = "true"
    return resp
