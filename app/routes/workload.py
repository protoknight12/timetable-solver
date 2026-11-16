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


WEEKS = ("A", "B")


def _ab(a: int, b: int) -> int | str:
    """One number when both weeks agree, else "A: 20 / B: 18" (BE-18)."""
    return a if a == b else f"A: {a} / B: {b}"


def _merge(a: dict, b: dict) -> dict:
    """Stats of the two weeks as one row.

    shortcut: when A and B differ, hours, days, gaps and the per-day numbers show the
    worse (larger) week and ``periods_text`` shows both; upgrade: a column per week.
    """
    per_day = {}
    for d in sorted(a["per_day"] | b["per_day"]):
        x, y = a["per_day"].get(d), b["per_day"].get(d)
        both = [z for z in (x, y) if z]
        per_day[d] = {
            "periods": max(z["periods"] for z in both),
            "first": min(z["first"] for z in both),
            "last": max(z["last"] for z in both),
            "gaps": max(z["gaps"] for z in both),
        }
    firsts = [z["first"] for z in (a, b) if z["first"] is not None]
    return {
        **a,
        **{k: max(a[k], b[k]) for k in ("periods", "days", "gaps", "worst_gaps")},
        "courses": a["courses"],
        "classes": a["classes"],
        "first": min(firsts, default=None),
        "last": max((z["last"] for z in (a, b) if z["last"] is not None), default=None),
        "per_day": per_day,
        "periods_text": _ab(a["periods"], b["periods"]),
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
                selectinload(CoursePart.course),
                selectinload(CoursePart.groups),
                selectinload(CoursePart.teachers),
                selectinload(CoursePart.subject),
            )
        )
    }
    # teacher -> week -> day -> periods taught; an every-week lesson counts in both weeks
    slots: dict[int, dict[str, dict[int, set[int]]]] = {}
    courses: dict[int, set] = {}
    classes: dict[int, set] = {}
    subjects: dict[
        int, dict[str, dict[str, int]]
    ] = {}  # teacher -> week -> subject -> periods
    for les in db.session.scalars(select(Lesson).where(Lesson.job_id == job_id)):
        part = parts[les.part_id]
        for t in part.teachers:
            courses.setdefault(t.id, set()).add(les.course_id)
            classes.setdefault(t.id, set()).update(g.class_id for g in part.groups)
            for w in WEEKS:
                if part.course.week not in (None, w):
                    continue
                day = (
                    slots.setdefault(t.id, {})
                    .setdefault(w, {})
                    .setdefault(les.day, set())
                )
                day.update(range(les.start_period, les.start_period + les.length))
                subj = subjects.setdefault(t.id, {}).setdefault(w, {})
                name = part.subject.name
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
    out = {}
    for t in db.session.scalars(select(Teacher).order_by(Teacher.name)):
        a, b = (
            _stats(
                slots.get(t.id, {}).get(w, {}),
                courses.get(t.id, set()),
                classes.get(t.id, set()),
            )
            for w in WEEKS
        )
        s = _merge(a, b)
        s["name"] = t.name
        by_week = [subjects.get(t.id, {}).get(w, {}) for w in WEEKS]
        s["subjects"] = {
            n: _ab(by_week[0].get(n, 0), by_week[1].get(n, 0))
            for n in sorted(by_week[0] | by_week[1])
        }
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
        if r["periods_text"] != r["periods"]:
            notes = "; ".join(filter(None, [f"часове: {r['periods_text']}", notes]))
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
