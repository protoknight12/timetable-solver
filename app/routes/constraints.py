"""Constraint list, manual entry and confirm / reject / delete (FE-06).

Anyone logged in may read the list; every write route is ``@admin_required``.

Assumptions:
- a constraint typed in by the admin is saved as ``confirmed`` at once (the admin
  is the confirmer); LLM proposals (BE-09) arrive as ``proposed`` rows;
- a rejected constraint can be confirmed again, a confirmed one rejected;
- shortcut: deleting has no confirmation page without JavaScript (HTMX asks via
  ``hx-confirm``); upgrade if the jury tests without JS.
"""

from __future__ import annotations

import json

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import select

from app import llm
from app.auth import admin_required, limiter
from app.constraint_forms import DAY_NAMES, FORMS, _course_name
from app.forms import room_types
from app.models import Constraint, Course, SchoolClass, Subject, Teacher, db

bp = Blueprint("constraints", __name__, url_prefix="/constraints")

STATUS_ORDER = {"proposed": 0, "confirmed": 1, "rejected": 2}
STATUS_LABELS = {
    "proposed": ("Предложено", "warning"),
    "confirmed": ("Потвърдено", "success"),
    "rejected": ("Отхвърлено", "danger"),
}


def _names() -> dict[str, dict[int, str]]:
    def of(model, label=lambda r: r.name):
        return {r.id: label(r) for r in db.session.scalars(select(model))}

    return {
        "teacher": of(Teacher),
        "subject": of(Subject),
        "class": of(SchoolClass),
        "course": of(Course, _course_name),
    }


def describe(c: Constraint, names: dict[str, dict[int, str]]) -> str:
    """One Bulgarian line for the list. Malformed params (e.g. from an LLM) show as JSON."""

    def name(kind, i):
        return names[kind].get(i, f"№{i} (изтрит)")

    def day(d):
        return DAY_NAMES.get(d, str(d))

    def scope(s):
        if s == "all":
            return "Всички класове"
        return (
            f"Клас {name('class', s['class_id'])}"
            if "class_id" in s
            else f"{s['grade']}. клас"
        )

    p = c.params
    try:
        match c.type:
            case "teacher_available_days":
                days = ", ".join(day(d) for d in p["days"])
                return f"{name('teacher', p['teacher_id'])}: работи само в {days}"
            case "teacher_unavailable_slots":
                slots = ", ".join(
                    f"{day(s['day'])}, {s['period']}. час" for s in p["slots"]
                )
                return f"{name('teacher', p['teacher_id'])}: не може в {slots}"
            case "class_unavailable_slots" | "subject_unavailable_slots":
                slots = ", ".join(
                    f"{day(s['day'])}, {s['period']}. час" for s in p["slots"]
                )
                who = (
                    f"Клас {name('class', p['class_id'])}"
                    if "class_id" in p
                    else f"Предмет {name('subject', p['subject_id'])}"
                )
                return f"{who}: без часове в {slots}"
            case "teacher_max_gaps":
                return (
                    f"{name('teacher', p['teacher_id'])}: "
                    f"най-много {p['max']} прозореца на ден"
                )
            case "teacher_min_lessons_per_day":
                return (
                    f"{name('teacher', p['teacher_id'])}: "
                    f"най-малко {p['min']} часа в ден, в който преподава"
                )
            case "room_type_required":
                who = (
                    f"Предмет {name('subject', p['subject_id'])}"
                    if p.get("subject_id")
                    else name("course", p["course_id"])
                )
                return f"{who}: стая от тип „{p['room_type']}“"
            case "max_lessons_per_day":
                return f"{scope(p['scope'])}: най-много {p['max']} часа на ден"
            case "max_same_subject_per_day":
                return (
                    f"{scope(p['scope'])}: най-много {p['max']} часа "
                    f"по {name('subject', p['subject_id'])} на ден"
                )
            case "teacher_max_lessons_per_day":
                return (
                    f"{name('teacher', p['teacher_id'])}: "
                    f"най-много {p['max']} часа на ден"
                )
            case "no_gaps":
                return f"{scope(p['scope'])}: без прозорци"
            case "day_starts_first_period":
                return f"{scope(p['scope'])}: денят започва от първия час"
            case "periods_reserved_for_subjects":
                periods = ", ".join(f"{n}." for n in p["periods"])
                subjects = ", ".join(name("subject", i) for i in p["subject_ids"])
                return f"{periods} час: само {subjects}"
            case "fixed_slot":
                blocks = ", ".join(
                    f"{day(b['day'])}, от {b['start_period']}. час" for b in p["blocks"]
                )
                return f"{name('course', p['course_id'])}: {blocks}"
    except (KeyError, TypeError, AttributeError):
        pass
    return json.dumps(p, ensure_ascii=False)


def _label(c: Constraint) -> str:
    form = FORMS.get(c.type)
    return form.label if form else c.type


def _row(c: Constraint) -> str:
    return render_template(
        "constraints/_row.html",
        c=c,
        label=_label(c),
        desc=describe(c, _names()),
        statuses=STATUS_LABELS,
    )


@bp.get("/")
@login_required
def list_view():
    items = sorted(
        db.session.scalars(select(Constraint)).all(),
        key=lambda c: (STATUS_ORDER.get(c.status, 3), c.id),
    )
    names = _names()
    rows = [(c, _label(c), describe(c, names)) for c in items]
    return render_template(
        "constraints/list.html",
        rows=rows,
        forms=FORMS,
        statuses=STATUS_LABELS,
        llm_enabled=current_app.config["LLM_ENABLED"],
        max_chars=llm.MAX_CHARS,
        active="constraints",
    )


@bp.route("/new/<kind>", methods=["GET", "POST"])
@admin_required
def new(kind):
    if kind not in FORMS:
        abort(404)
    form = FORMS[kind]()
    if form.validate_on_submit():
        c = Constraint(
            type=kind,
            hard=form.hard.data,
            weight=None if form.hard.data else form.weight.data,
            params=form.params(),
            status="confirmed",
            source="manual",
        )
        db.session.add(c)
        db.session.commit()
        flash("Ограничението е добавено и потвърдено.", "success")
        return redirect(url_for("constraints.list_view"))
    return render_template(
        "constraints/form.html",
        form=form,
        heading=f"Ново ограничение: {form.label}",
        cancel_url=url_for("constraints.list_view"),
        active="constraints",
        types=room_types(),
    )


def _done(c: Constraint | None, message: str):
    """HTMX swaps the row (or removes it); a plain form post flashes and redirects."""
    if request.headers.get("HX-Request"):
        return _row(c) if c else ""
    flash(message, "success")
    return redirect(url_for("constraints.list_view"))


@bp.post("/<int:item_id>/status")
@admin_required
def set_status(item_id):
    c = db.get_or_404(Constraint, item_id)
    status = request.form.get("status")
    if status not in ("confirmed", "rejected"):
        abort(400)
    c.status = status
    db.session.commit()
    return _done(c, f"Ограничение №{c.id}: {STATUS_LABELS[status][0].lower()}.")


@bp.post("/<int:item_id>/delete")
@admin_required
def delete(item_id):
    c = db.get_or_404(Constraint, item_id)
    db.session.delete(c)
    db.session.commit()
    return _done(None, f"Ограничение №{item_id} е изтрито.")


@bp.post("/propose")
@admin_required
@limiter.limit(
    lambda: current_app.config["LLM_RATE_LIMIT"],
    key_func=lambda: str(current_user.get_id()),
)
@limiter.limit(
    lambda: current_app.config["LLM_DAILY_CAP"],
    key_func=lambda: "llm",
    scope="llm-daily",
)
def propose():
    """Text -> proposed constraints (BE-09). The text box is on the list page (FE-09)."""
    if not current_app.config["LLM_ENABLED"]:
        abort(404)
    text = request.form.get("text", "").strip()
    if not text:
        flash("Въведете текст на заявката.", "warning")
    elif len(text) > llm.MAX_CHARS:
        flash(f"Текстът е твърде дълъг (най-много {llm.MAX_CHARS} знака).", "danger")
    else:
        try:
            saved, unclear = llm.propose_constraints(text)
        except llm.LlmError as e:
            flash(str(e), "danger")
        else:
            if saved:
                flash(
                    f"Предложени ограничения: {len(saved)}. Прегледайте ги и ги потвърдете или отхвърлете.",
                    "success",
                )
            if unclear:
                flash(
                    "Не са разбрани: " + "; ".join(f"„{u}“" for u in unclear), "warning"
                )
            if not saved and not unclear:
                flash("Не бяха намерени ограничения в текста.", "info")
    return redirect(url_for("constraints.list_view"))
