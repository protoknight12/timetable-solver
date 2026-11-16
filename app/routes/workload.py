"""Workload report per teacher (FE-17). A report, not a validity check: it counts what is in the timetable."""

from io import BytesIO

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.constraint_forms import DAY_NAMES
from app.models import Constraint, CoursePart, Lesson, Teacher, db
from app.routes.solve import HAS_TIMETABLE, shown_job

bp = Blueprint("workload", __name__)

# key -> (column title, sortable value). Order is the column order of page and export.
COLUMNS = {
    "name": "Учител",
    "periods": "Часове",
    "courses": "Курсове",
    "classes": "Класове",
    "days": "Дни",
    "gaps": "Прозорци",
    "worst_gaps": "Най-много прозорци в ден",
    "first": "Най-ранен час",
    "last": "Най-късен час",
}


def _stats(slots: dict[int, set[int]], courses: set, classes: set) -> dict:
    """slots: day -> periods taught. Gaps = free periods between the first and last lesson of a day."""
    per_day = {
        d: {
            "periods": len(ps),
            "first": min(ps),
            "last": max(ps),
            "gaps": max(ps) - min(ps) + 1 - len(ps),
        }
        for d, ps in sorted(slots.items())
    }
    days = per_day.values()
    return {
        "periods": sum(x["periods"] for x in days),
        "courses": len(courses),
        "classes": len(classes),
        "days": len(per_day),
        "gaps": sum(x["gaps"] for x in days),
        "worst_gaps": max((x["gaps"] for x in days), default=0),
        "first": min((x["first"] for x in days), default=None),
        "last": max((x["last"] for x in days), default=None),
        "per_day": per_day,
        "subjects": {},
        "over": [],
    }


def compute(job_id: int) -> dict[int, dict]:
    """teacher id -> numbers. Joint lessons count once per teacher; a slot is counted once."""
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
    slots: dict[int, dict[int, set[int]]] = {}
    courses: dict[int, set] = {}
    classes: dict[int, set] = {}
    subjects: dict[int, dict[str, int]] = {}
    for les in db.session.scalars(select(Lesson).where(Lesson.job_id == job_id)):
        part = parts[les.part_id]
        for t in part.teachers:
            day = slots.setdefault(t.id, {}).setdefault(les.day, set())
            day.update(range(les.start_period, les.start_period + les.length))
            courses.setdefault(t.id, set()).add(les.course_id)
            classes.setdefault(t.id, set()).update(g.class_id for g in part.groups)
            name = part.subject.name
            subj = subjects.setdefault(t.id, {})
            subj[name] = subj.get(name, 0) + les.length
    # shortcut: only teacher_max_lessons_per_day is flagged; flag gaps above a
    # teacher_max_gaps limit here once that constraint type exists (BE-19).
    limits = {}
    for c in db.session.scalars(
        select(Constraint).where(
            Constraint.status == "confirmed",
            Constraint.type == "teacher_max_lessons_per_day",
        )
    ):
        t, mx = c.params["teacher_id"], c.params["max"]
        limits[t] = min(mx, limits.get(t, mx))
    # shortcut: A/B-week differences are missing until BE-18 (A/B weeks) is merged.
    out = {}
    for t in db.session.scalars(select(Teacher).order_by(Teacher.name)):
        s = _stats(
            slots.get(t.id, {}), courses.get(t.id, set()), classes.get(t.id, set())
        )
        s["name"] = t.name
        s["subjects"] = dict(sorted(subjects.get(t.id, {}).items()))
        s["limit"] = limits.get(t.id)
        s["over"] = [
            (d, x["periods"])
            for d, x in s["per_day"].items()
            if s["limit"] and x["periods"] > s["limit"]
        ]
        out[t.id] = s
    return out


def _visible(job) -> tuple[dict[int, dict] | None, bool]:
    """(rows this user may see, user is admin). None = a viewer without a linked teacher."""
    admin = current_user.role == "admin"
    if not admin and not current_user.teacher_id:
        return None, admin
    rows = compute(job.id)
    if not admin:
        rows = {k: v for k, v in rows.items() if k == current_user.teacher_id}
    return rows, admin


def _job():
    job = shown_job(current_user.role == "admin", request.args.get("job", type=int))
    ok = job and job.status == "finished" and job.solver_status in HAS_TIMETABLE
    return job if ok else None


def _days():
    return {d: n.capitalize() for d, n in DAY_NAMES.items()}


@bp.get("/workload")
@login_required
def index():
    job = _job()
    if current_user.role != "admin" and not current_user.teacher_id:
        return render_template("workload/index.html", rows=None, active="workload")
    if job is None:
        flash("Все още няма изградено разписание.", "warning")
        return redirect(url_for("solve.timetable"))
    rows, _ = _visible(job)
    key = request.args.get("sort", "name")
    if key not in COLUMNS:
        key = "name"
    desc = request.args.get("dir") == "desc"
    # A teacher without lessons has no earliest/latest period: sort those last.
    ordered = sorted(
        rows.items(),
        key=lambda kv: (
            (kv[1][key] is None, kv[1][key] or 0)
            if key not in ("name",)
            else (False, kv[1]["name"].casefold())
        ),
        reverse=desc,
    )
    total = {k: sum(r[k] for r in rows.values()) for k in ("periods", "gaps")}
    return render_template(
        "workload/index.html",
        rows=ordered,
        total=total,
        columns=COLUMNS,
        sort=key,
        desc=desc,
        job=job,
        active="workload",
    )


@bp.get("/workload/<int:teacher_id>")
@login_required
def detail(teacher_id: int):
    job = _job()
    if current_user.role != "admin" and current_user.teacher_id != teacher_id:
        abort(403)
    if job is None:
        flash("Все още няма изградено разписание.", "warning")
        return redirect(url_for("solve.timetable"))
    rows, _ = _visible(job)
    if teacher_id not in rows:
        abort(404)
    return render_template(
        "workload/detail.html",
        t=rows[teacher_id],
        teacher_id=teacher_id,
        day_names=_days(),
        job=job,
        active="workload",
    )


@bp.get("/workload.xlsx")
@login_required
def export_xlsx():
    job = _job()
    if current_user.role != "admin" and not current_user.teacher_id:
        abort(403)
    if job is None:
        flash("Все още няма изградено разписание за изтегляне.", "warning")
        return redirect(url_for("solve.timetable"))
    rows, _ = _visible(job)
    wb = Workbook()
    ws = wb.active
    ws.title = "Натовареност"
    ws.append([*COLUMNS.values(), "Бележки"])
    for r in rows.values():
        notes = "; ".join(
            f"над лимита ({n} от {r['limit']}) в {_days()[d]}" for d, n in r["over"]
        )
        ws.append([r[k] for k in COLUMNS] + [notes])
    if len(rows) > 1:
        ws.append(
            ["Общо", sum(r["periods"] for r in rows.values())]
            + [None] * 3
            + [sum(r["gaps"] for r in rows.values())]
        )
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.column_dimensions["A"].width = 28
    ws.freeze_panes = "B2"
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name="natovarenost.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
