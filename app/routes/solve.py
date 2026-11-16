"""Start a solve job, report its progress (BE-08) and show the result (FE-08)."""

from __future__ import annotations

import re
from io import BytesIO

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from redis import Redis
from rq import Queue
from sqlalchemy import DateTime, func, select, type_coerce, update
from sqlalchemy.orm import selectinload

from app.auth import admin_required, limiter
from app.constraint_forms import DAY_NAMES
from app.history import diff_lessons, how_made
from app.jobs import placements, run_solve, solve_in_thread
from app.models import (
    Constraint,
    Course,
    CoursePart,
    Lesson,
    Period,
    Room,
    SchoolClass,
    SolveJob,
    Subject,
    Teacher,
    User,
    Violation,
    db,
)
from app.routes.constraints import _names as constraint_names
from app.routes.constraints import describe
from app.solve_text import FALLBACK_MESSAGE, JOB_STATUS, MESSAGES, SOLVER_STATUS
from app.solver_input import build_solver_input
from app.subject_colors import subject_color, subject_css, text_color
from checker import check
from schema.contract import LessonPlacement, SolveResult
from solver import count_moved

bp = Blueprint("solve", __name__)

ACTIVE = ("queued", "running")
# Only these solver statuses carry a timetable (same rule as checker A1).
HAS_TIMETABLE = ("OPTIMAL", "FEASIBLE")

# Extra seconds on top of the solver limit for loading, checking and saving.
TIMEOUT_MARGIN_S = 60


def _enqueue(job_id: int, timeout_s: int) -> None:
    enqueue = current_app.config["SOLVE_ENQUEUE"]
    if enqueue:
        enqueue(job_id, timeout_s)
        return
    if current_app.config["SOLVE_INLINE"]:
        solve_in_thread(job_id)
        return
    queue = Queue(connection=Redis.from_url(current_app.config["REDIS_URL"]))
    queue.enqueue(run_solve, job_id, job_timeout=timeout_s)


def _previous_job(before_id: int | None = None) -> SolveJob | None:
    """Newest finished job with a timetable (older than ``before_id`` if given)."""
    query = select(SolveJob).where(
        SolveJob.status == "finished", SolveJob.solver_status.in_(HAS_TIMETABLE)
    )
    if before_id is not None:
        query = query.where(SolveJob.id < before_id)
    return db.session.scalar(query.order_by(SolveJob.id.desc()))


def published_job() -> SolveJob | None:
    return db.session.scalar(select(SolveJob).where(SolveJob.published_at.isnot(None)))


def shown_job(for_admin: bool, job_id: int | None = None) -> SolveJob | None:
    """The one job a page shows. Reuse this for every timetable page (FE-15..17).

    Admin: the chosen job, else the newest (a draft, so progress stays visible).
    Everyone else: the published version, else the newest one with a timetable.
    """
    if for_admin:
        return (job_id and db.session.get(SolveJob, job_id)) or db.session.scalar(
            select(SolveJob).order_by(SolveJob.id.desc())
        )
    return published_job() or _previous_job()


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
    # The checkbox is on by default in the page; no field means a fresh solve.
    previous = _previous_job() if request.form.get("keep_previous") else None
    job = SolveJob(
        status="queued",
        time_limit_s=limit,
        base_job_id=previous and previous.id,
        created_by_id=current_user.id,
    )
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
    return _format_violations(
        db.session.scalars(
            select(Violation).where(Violation.job_id == job_id).order_by(Violation.id)
        )
    )


def _format_violations(violations) -> list[dict]:
    """Bulgarian rows for saved or not yet saved ``Violation`` objects."""
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
    for v in violations:
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


def _subject_fields(subject: Subject) -> dict:
    # The short name (if any) is what the cell shows; the page and the export share this.
    return {
        "subject": subject.short_name or subject.name,
        "subject_full": subject.name,
        "subject_id": subject.id,
        "color": subject_color(subject),
    }


def _pinned_courses() -> set[int]:
    return {
        c.params["course_id"]
        for c in db.session.scalars(
            select(Constraint).where(
                Constraint.type == "fixed_slot", Constraint.status == "confirmed"
            )
        )
    }


def _grid(
    job_id: int,
    class_id: int | None = None,
    teacher_id: int | None = None,
    changed: set[tuple[int, int, int, int]] = frozenset(),
) -> dict[tuple[int, int], list[dict]]:
    """(day, period) -> lessons of this class or teacher; a split slot has several.

    ``changed`` holds (part_id, day, start_period, length) of blocks to flag.
    """
    parts = db.session.scalars(
        select(CoursePart).options(
            selectinload(CoursePart.groups),
            selectinload(CoursePart.teachers),
            selectinload(CoursePart.subject),
        )
    )
    parts = {p.id: p for p in parts}
    names = _entity_names()
    rooms = names["room"]
    pinned = _pinned_courses()
    cells: dict[tuple[int, int], list[dict]] = {}
    for les in db.session.scalars(select(Lesson).where(Lesson.job_id == job_id)):
        part = parts[les.part_id]
        if teacher_id is not None:
            if teacher_id not in {t.id for t in part.teachers}:
                continue
            # Teacher view reuses the "teachers" slot of the cell for the classes
            # (and groups) taught, so the template and the export stay unchanged.
            by_class: dict[int, list] = {}
            for g in part.groups:
                by_class.setdefault(g.class_id, []).append(g)
            label = ", ".join(
                names["class"][cid]
                + (
                    ""
                    if any(g.is_whole for g in gs)
                    else " (" + ", ".join(sorted(g.name for g in gs)) + ")"
                )
                for cid, gs in sorted(by_class.items())
            )
            entry = {
                **_subject_fields(part.subject),
                "teachers": label,
                "room": rooms.get(les.room_id, ""),
                "group": "",
                "lesson_id": les.id,
                "pinned": les.course_id in pinned,
                "changed": (part.id, les.day, les.start_period, les.length) in changed,
            }
            for period in range(les.start_period, les.start_period + les.length):
                cells.setdefault((les.day, period), []).append(entry)
            continue
        mine = [g for g in part.groups if g.class_id == class_id]
        if not mine:
            continue
        entry = {
            **_subject_fields(part.subject),
            "teachers": ", ".join(sorted(t.name for t in part.teachers)),
            "room": rooms.get(les.room_id, ""),
            "lesson_id": les.id,
            "pinned": les.course_id in pinned,
            # The whole class needs no group label.
            "group": ""
            if any(g.is_whole for g in mine)
            else ", ".join(sorted(g.name for g in mine)),
            "changed": (part.id, les.day, les.start_period, les.length) in changed,
        }
        for period in range(les.start_period, les.start_period + les.length):
            cells.setdefault((les.day, period), []).append(entry)
    return cells


def _conflict_rows(job_id: int) -> tuple[list[dict], list[dict]]:
    """(conflicting constraints named by the solver, confirmed hard ones to try disabling)."""
    names = constraint_names()

    def row(c: Constraint) -> dict:
        return {"id": c.id, "text": c.original_text or describe(c, names)}

    ids = db.session.scalars(
        select(Violation.constraint_id).where(
            Violation.job_id == job_id, Violation.type == "infeasible_core"
        )
    ).all()
    found = db.session.scalars(
        select(Constraint).where(Constraint.id.in_(ids)).order_by(Constraint.id)
    ).all()
    confirmed = db.session.scalars(
        select(Constraint)
        .where(Constraint.status == "confirmed", Constraint.hard.is_(True))
        .order_by(Constraint.id)
    ).all()
    return [row(c) for c in found], [row(c) for c in confirmed]


@bp.get("/timetable")
@login_required
def timetable():
    is_admin = current_user.role == "admin"
    job = shown_job(is_admin, request.args.get("job", type=int))
    published = published_job()
    classes = db.session.scalars(select(SchoolClass).order_by(SchoolClass.id)).all()
    class_id = request.args.get("class", type=int)
    current = next((c for c in classes if c.id == class_id), None) or (
        classes[0] if classes else None
    )
    teachers = db.session.scalars(select(Teacher).order_by(Teacher.name)).all()
    teacher_id = request.args.get("teacher", type=int)
    teacher = next((t for t in teachers if t.id == teacher_id), None)
    ctx = {
        "job": job,
        "classes": classes,
        "current": current,
        "teachers": teachers,
        "teacher": teacher,
        "active": "timetable",
        "published": published,
        "can_keep_previous": _previous_job() is not None,
    }
    if job and job.status == "finished" and job.solver_status in HAS_TIMETABLE:
        before = _previous_job(job.id)
        moved_blocks = {
            (d["after"].part_id, *_slot(d["after"]))
            for d in diff_lessons(placements(before and before.id), placements(job.id))
            if before and d["kind"] in ("moved", "room")
        }
        ctx |= {
            "cells": _grid(job.id, current.id, teacher and teacher.id, moved_blocks)
            if current or teacher
            else {},
            "days": range(1, current_app.config["SCHOOL_DAYS"] + 1),
            "periods": db.session.scalars(select(Period).order_by(Period.number)).all(),
            "back": {
                "class": current and current.id,
                "teacher": teacher and teacher.id,
            },
            "violations": _violation_rows(job.id),
            "subject_styles": subject_css(db.session.scalars(select(Subject))),
        }
        if job.base_job_id:
            ctx["moved"] = count_moved(placements(job.base_job_id), placements(job.id))
    elif job and job.status == "finished":
        ctx["conflicts"], ctx["try_disabling"] = _conflict_rows(job.id)
    return render_template(
        "solve/timetable.html",
        job_status=JOB_STATUS,
        solver_status=SOLVER_STATUS,
        day_names={d: n.capitalize() for d, n in DAY_NAMES.items()},
        active_states=ACTIVE,
        **ctx,
    )


@bp.get("/timetable.xlsx")
@login_required
def export_xlsx():
    """One sheet per class, same data as the page grid (FE-10)."""
    job = shown_job(current_user.role == "admin", request.args.get("job", type=int))
    if not (job and job.status == "finished" and job.solver_status in HAS_TIMETABLE):
        flash("Все още няма изградено разписание за изтегляне.", "warning")
        return redirect(url_for("solve.timetable"))
    periods = db.session.scalars(select(Period).order_by(Period.number)).all()
    days = range(1, current_app.config["SCHOOL_DAYS"] + 1)
    wb, used = Workbook(), set()
    wb.remove(wb.active)
    for c in db.session.scalars(select(SchoolClass).order_by(SchoolClass.id)):
        # Excel forbids []:*?/\ and >31 chars; names must also be unique (any case).
        base = re.sub(r"[\[\]:*?/\\]", "_", c.name).strip("'")[:28] or "Клас"
        title, n = base, 1
        while title.lower() in used:
            n += 1
            title = f"{base[:28]} {n}"
        used.add(title.lower())
        ws = wb.create_sheet(title)
        ws.append(["Час"] + [DAY_NAMES[d].capitalize() for d in days])
        cells = _grid(job.id, c.id)
        for p in periods:
            row = [f"{p.number}\n{p.start_time:%H:%M}–{p.end_time:%H:%M}"]
            for d in days:
                row.append(
                    "\n\n".join(
                        l["subject"]
                        + "\n"
                        + " · ".join(
                            x for x in (l["teachers"], l["room"], l["group"]) if x
                        )
                        for l in cells.get((d, p.number), [])
                    )
                )
            ws.append(row)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for row in ws.iter_rows():
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        for r, p in enumerate(periods, 2):
            for d in days:
                # shortcut: a split cell (several lessons) has no single color, so it stays plain.
                lessons = cells.get((d, p.number), [])
                if len(lessons) == 1:
                    color = lessons[0]["color"]
                    cell = ws.cell(r, d + 1)
                    cell.fill = PatternFill("solid", fgColor=color[1:])
                    cell.font = Font(color=text_color(color)[1:])
        ws.column_dimensions["A"].width = 12
        for d in days:
            ws.column_dimensions[ws.cell(1, d + 1).column_letter].width = 28
        ws.freeze_panes = "B2"
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name="razpisanie.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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


def _slot(p) -> tuple[int, int, int]:
    return p.day, p.start_period, p.length


def _edited(job: SolveJob) -> bool:
    """A copy that differs from its base was edited by hand (FE-15), else restored.

    shortcut: an edit moved back to the base layout reads as a restore; a marker
    column would fix that, but needs the schema owner's approval.
    """
    return bool(job.base_job_id) and bool(
        diff_lessons(placements(job.base_job_id), placements(job.id))
    )


def _version(job_id: int) -> SolveJob:
    """A job that holds a timetable (a "version"), else 404."""
    job = db.session.get(SolveJob, job_id) or abort(404)
    if job.status != "finished" or job.solver_status not in HAS_TIMETABLE:
        abort(404)
    return job


@bp.get("/history")
@admin_required
def history():
    versions = db.session.scalars(
        select(SolveJob)
        .where(SolveJob.status == "finished", SolveJob.solver_status.in_(HAS_TIMETABLE))
        .order_by(SolveJob.id.desc())
    ).all()
    users = {u.id: u.username for u in db.session.scalars(select(User))}
    rows = []
    for i, job in enumerate(versions):
        prev = versions[i + 1] if i + 1 < len(versions) else None
        rows.append(
            {
                "job": job,
                "who": users.get(job.created_by_id, "—"),
                "how": how_made(job, _edited(job))[1],
                "moved": prev and count_moved(placements(prev.id), placements(job.id)),
            }
        )
    return render_template(
        "solve/history.html", rows=rows, solver_status=SOLVER_STATUS, active="timetable"
    )


@bp.get("/history/<int:job_id>")
@admin_required
def version(job_id: int):
    """Changes of a version against the previous one (or ?against=<id>)."""
    job = _version(job_id)
    against = request.args.get("against", type=int)
    other = _version(against) if against else _previous_job(job.id)
    names = _entity_names()
    parts = {
        p.id: p
        for p in db.session.scalars(
            select(CoursePart).options(
                selectinload(CoursePart.groups),
                selectinload(CoursePart.teachers),
                selectinload(CoursePart.subject),
            )
        )
    }
    class_id = request.args.get("class", type=int)
    teacher_id = request.args.get("teacher", type=int)
    by = "teacher" if request.args.get("by") == "teacher" else "class"

    def where(p) -> str:
        end = f"–{p.start_period + p.length - 1}" if p.length > 1 else ""
        room = names["room"].get(p.room_id)
        return f"{names['day'][p.day]}, {p.start_period}{end}. час" + (
            f", {room}" if room else ""
        )

    groups: dict[str, list[dict]] = {}
    counts = {"added": 0, "removed": 0, "moved": 0, "room": 0}
    for d in diff_lessons(placements(other and other.id), placements(job.id)):
        p = d["before"] or d["after"]
        part = parts[p.part_id]
        classes = sorted({names["class"][g.class_id] for g in part.groups})
        teachers = sorted(t.name for t in part.teachers)
        if class_id and class_id not in {g.class_id for g in part.groups}:
            continue
        if teacher_id and teacher_id not in {t.id for t in part.teachers}:
            continue
        counts[d["kind"]] += 1
        row = {
            "kind": d["kind"],
            "subject": part.subject.name,
            "before": d["before"] and where(d["before"]),
            "after": d["after"] and where(d["after"]),
        }
        for key in (teachers if by == "teacher" else classes) or ["—"]:
            groups.setdefault(key, []).append(row)
    return render_template(
        "solve/version.html",
        job=job,
        other=other,
        groups=dict(sorted(groups.items())),
        counts=counts,
        by=by,
        classes=db.session.scalars(select(SchoolClass).order_by(SchoolClass.id)).all(),
        teachers=db.session.scalars(select(Teacher).order_by(Teacher.name)).all(),
        class_id=class_id,
        teacher_id=teacher_id,
        how=how_made(job, _edited(job))[1],
        solver_status=SOLVER_STATUS,
        active="timetable",
    )


@bp.post("/jobs/<int:job_id>/publish")
@admin_required
def publish(job_id: int):
    job = _version(job_id)
    # One transaction: unpublish the others and publish this one.
    db.session.execute(update(SolveJob).values(published_at=None))
    job.published_at = func.now()
    db.session.commit()
    flash(f"Версия №{job.id} е публикувана.", "success")
    return redirect(url_for("solve.history"))


@bp.post("/jobs/<int:job_id>/restore")
@admin_required
def restore(job_id: int):
    """Copy a version into a NEW one; history is never rewritten."""
    src = _version(job_id)
    new = SolveJob(
        status="finished",
        finished_at=func.now(),
        solver_status=src.solver_status,
        objective=src.objective,
        base_job_id=src.id,
        created_by_id=current_user.id,
    )
    db.session.add(new)
    db.session.flush()
    for les in db.session.scalars(select(Lesson).where(Lesson.job_id == src.id)):
        db.session.add(
            Lesson(
                job_id=new.id,
                course_id=les.course_id,
                part_id=les.part_id,
                day=les.day,
                start_period=les.start_period,
                length=les.length,
                room_id=les.room_id,
            )
        )
    for v in db.session.scalars(select(Violation).where(Violation.job_id == src.id)):
        db.session.add(
            Violation(
                job_id=new.id,
                type=v.type,
                entity_ids=v.entity_ids,
                message_key=v.message_key,
                hard=v.hard,
                constraint_id=v.constraint_id,
            )
        )
    db.session.commit()
    flash(f"Версия №{src.id} е възстановена като нова версия №{new.id}.", "success")
    return redirect(url_for("solve.history"))


# --- FE-15: manual editing (a human action only; the LLM never places lessons) ---


def _lesson(lesson_id: int) -> Lesson:
    """A lesson of a saved version, else 404."""
    les = db.session.get(Lesson, lesson_id) or abort(404)
    _version(les.job_id)
    return les


def _back(form) -> dict:
    """Class or teacher the page was showing, so the redirect returns to it."""
    return {k: form.get(k, type=int) for k in ("class", "teacher")}


def _to_timetable(job_id: int, form):
    return redirect(url_for("solve.timetable", job=job_id, **_back(form)))


def _block_index(les: Lesson) -> int:
    """Index of this row among the rows of its part = its block number (checker A3)."""
    ids = db.session.scalars(
        select(Lesson.id)
        .where(Lesson.job_id == les.job_id, Lesson.part_id == les.part_id)
        .order_by(Lesson.id)
    ).all()
    return ids.index(les.id)


@bp.get("/lessons/<int:lesson_id>/move")
@admin_required
def move_form(lesson_id: int):
    """No-JavaScript way to move a lesson: day, period and room selects."""
    les = _lesson(lesson_id)
    part = db.session.get(CoursePart, les.part_id)
    return render_template(
        "solve/move.html",
        les=les,
        subject=part.subject.name,
        days=_entity_names()["day"],
        periods=db.session.scalars(select(Period).order_by(Period.number)).all(),
        rooms=db.session.scalars(select(Room).order_by(Room.name)).all(),
        back={k: v for k, v in _back(request.args).items() if v},
        active="timetable",
    )


@bp.post("/lessons/<int:lesson_id>/move")
@admin_required
def move(lesson_id: int):
    """Move a lesson block (with all parts of its course) into a NEW version.

    The checker runs on the whole edited timetable first. Hard violations that
    the edited version did not already have need a confirmation ("confirm=1").
    """
    les = _lesson(lesson_id)
    src = db.session.get(SolveJob, les.job_id)
    day = request.form.get("day", type=int)
    start = request.form.get("start_period", type=int)
    room = request.form.get("room_id", type=int)
    numbers = set(db.session.scalars(select(Period.number)))
    valid = (
        day in range(1, current_app.config["SCHOOL_DAYS"] + 1)
        and start is not None
        and all(p in numbers for p in range(start, start + les.length))
        and (room is None or db.session.get(Room, room) is not None)
    )
    if not valid:
        flash(
            "Урокът не може да бъде поставен там: неверен ден, час или стая.", "danger"
        )
        return _to_timetable(src.id, request.form)
    if (day, start, room or les.room_id) == (les.day, les.start_period, les.room_id):
        flash("Урокът вече е на това място.", "info")
        return _to_timetable(src.id, request.form)

    block = _block_index(les)
    seen: dict[int, int] = {}  # part_id -> rows seen so far = block number of the next
    edited = []
    for x in db.session.scalars(
        select(Lesson).where(Lesson.job_id == src.id).order_by(Lesson.id)
    ):
        i, seen[x.part_id] = seen.get(x.part_id, 0), seen.get(x.part_id, 0) + 1
        moves = x.course_id == les.course_id and i == block
        edited.append(
            LessonPlacement(
                x.course_id,
                x.part_id,
                day if moves else x.day,
                start if moves else x.start_period,
                x.length,
                (room or x.room_id) if x.id == les.id else x.room_id,
            )
        )
    inp = build_solver_input(db.session)
    if inp.problems():
        flash(
            "Входните данни не са пълни. Прегледайте данните и ограниченията.", "danger"
        )
        return _to_timetable(src.id, request.form)
    violations = [
        Violation(
            type=v.rule,
            entity_ids=[list(e) for e in v.entities],
            message_key=v.message_key,
            hard=v.hard,
            constraint_id=v.constraint_id,
        )
        for v in check(inp, SolveResult(src.solver_status, tuple(edited)))
    ]

    def key(v) -> tuple:
        return v.message_key, v.constraint_id, tuple(map(tuple, v.entity_ids))

    known = {
        key(v)
        for v in db.session.scalars(select(Violation).where(Violation.job_id == src.id))
    }
    new_hard = [v for v in violations if v.hard and key(v) not in known]
    if new_hard and not request.form.get("confirm"):
        return render_template(
            "solve/move_confirm.html",
            les=les,
            rows=_format_violations(new_hard),
            form=request.form,
            back={k: v for k, v in _back(request.form).items() if v},
            active="timetable",
        )

    new = SolveJob(
        status="finished",
        finished_at=func.now(),
        solver_status=src.solver_status,
        base_job_id=src.id,
        created_by_id=current_user.id,
    )
    db.session.add(new)
    db.session.flush()
    db.session.add_all(
        Lesson(
            job_id=new.id,
            course_id=x.course_id,
            part_id=x.part_id,
            day=x.day,
            start_period=x.start_period,
            length=x.length,
            room_id=x.room_id,
        )
        for x in edited
    )
    for v in violations:
        v.job_id = new.id
    db.session.add_all(violations)
    db.session.commit()
    flash(f"Промяната е запазена като нова версия №{new.id}.", "success")
    return _to_timetable(new.id, request.form)


def _fixed_slots(course_id: int) -> list[Constraint]:
    return [
        c
        for c in db.session.scalars(
            select(Constraint).where(Constraint.type == "fixed_slot")
        )
        if c.params.get("course_id") == course_id
    ]


@bp.post("/lessons/<int:lesson_id>/pin")
@admin_required
def pin(lesson_id: int):
    """Confirmed fixed_slot for the lesson's course at its current blocks."""
    les = _lesson(lesson_id)
    course = db.session.get(Course, les.course_id)
    first_part = db.session.scalar(
        select(func.min(Lesson.part_id)).where(
            Lesson.job_id == les.job_id, Lesson.course_id == course.id
        )
    )
    rows = db.session.scalars(
        select(Lesson)
        .where(Lesson.job_id == les.job_id, Lesson.part_id == first_part)
        .order_by(Lesson.id)
    ).all()
    if len(rows) != len(course.block_sizes):
        flash(
            "Урокът не може да бъде закачен: блоковете не съвпадат с учебния план.",
            "danger",
        )
        return _to_timetable(les.job_id, request.form)
    params = {
        "course_id": course.id,
        "blocks": [{"day": r.day, "start_period": r.start_period} for r in rows],
    }
    existing = _fixed_slots(course.id)
    if existing:  # update instead of adding a duplicate
        existing[0].params, existing[0].status = params, "confirmed"
    else:
        db.session.add(
            Constraint(
                type="fixed_slot",
                params=params,
                hard=True,
                status="confirmed",
                source="manual",
            )
        )
    db.session.commit()
    flash(
        "Урокът е закачен: следващото изчисление ще го запази на това място.", "success"
    )
    return _to_timetable(les.job_id, request.form)


@bp.post("/lessons/<int:lesson_id>/unpin")
@admin_required
def unpin(lesson_id: int):
    les = _lesson(lesson_id)
    for c in _fixed_slots(les.course_id):
        db.session.delete(c)
    db.session.commit()
    flash("Урокът е откачен.", "success")
    return _to_timetable(les.job_id, request.form)
