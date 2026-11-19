from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_user, logout_user
from sqlalchemy import func, select

from app import llm
from app.auth import authenticate, create_user, limiter
from app.forms import RegisterForm
from app.models import (
    Constraint,
    Course,
    Lesson,
    SchoolClass,
    SolveJob,
    Teacher,
    Violation,
    db,
)

bp = Blueprint("pages", __name__, template_folder="templates")


def _count(model, *where) -> int:
    return db.session.scalar(select(func.count()).select_from(model).where(*where))


def _dashboard() -> dict:
    """Numbers and the latest run for the home page of a logged-in user."""
    admin = current_user.role == "admin"
    jobs = select(SolveJob).order_by(SolveJob.id.desc())
    if not admin:  # viewers only ever see published timetables
        jobs = jobs.where(SolveJob.published_at.is_not(None))
    counts = {
        "teachers": _count(Teacher),
        "classes": _count(SchoolClass),
        "courses": _count(Course),
        "constraints": _count(Constraint, Constraint.status == "confirmed"),
        "proposed": _count(Constraint, Constraint.status == "proposed"),
    }
    job = db.session.scalar(jobs.limit(1))
    lessons = _count(Lesson, Lesson.job_id == job.id) if job else 0
    hard = _count(Violation, Violation.job_id == job.id, Violation.hard) if job else 0
    has_timetable = bool(
        job
        and job.status == "finished"
        and job.solver_status in ("OPTIMAL", "FEASIBLE")
    )
    steps = [
        {
            "title": "Данни",
            "icon": "bi-database",
            "text": "Учители, класове, стаи и учебен план",
            "url": url_for("data.teachers_list"),
            "done": counts["courses"] > 0,
            "note": f"{counts['teachers']} учители, {counts['classes']} класа"
            if counts["courses"]
            else "Няма въведен учебен план",
        },
        {
            "title": "Ограничения",
            "icon": "bi-sliders",
            "text": "Потвърдете или отхвърлете предложените правила",
            "url": url_for("constraints.list_view"),
            "done": counts["proposed"] == 0,
            "note": f"{counts['proposed']} чакат потвърждение"
            if counts["proposed"]
            else f"{counts['constraints']} потвърдени",
        },
        {
            "title": "Разписание",
            "icon": "bi-calendar3",
            "text": "Изградете графика и го изтеглете",
            "url": url_for("solve.timetable"),
            "done": has_timetable,
            "note": f"Последно: изчисление №{job.id}" if job else "Още няма график",
        },
    ]
    # The first unfinished step is the one to do next.
    next_step = next((i for i, st in enumerate(steps) if not st["done"]), None)
    return {
        "counts": counts,
        "job": job,
        "lessons": lessons,
        "hard": hard,
        "has_timetable": has_timetable,
        "steps": steps,
        "next_step": next_step,
        "is_admin": admin,
        "llm_enabled": current_app.config["LLM_ENABLED"],
        "max_chars": llm.MAX_CHARS,
    }


@bp.route("/")
def index():
    if not current_user.is_authenticated:
        return render_template("index.html")
    return render_template("index.html", **_dashboard())


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config["LOGIN_RATE_LIMIT"], methods=["POST"])
def login():
    if request.method == "POST":
        user = authenticate(
            request.form.get("username", "")[:64],
            request.form.get("password", "")[:128],
        )
        if user:
            login_user(user, remember=bool(request.form.get("remember")))
            if user.teacher_id and user.role == "viewer":
                return redirect(url_for("solve.timetable", teacher=user.teacher_id))
            return redirect(url_for("pages.index"))
        # Never say which part was wrong.
        flash("Грешно потребителско име или парола.", "danger")
        return render_template("login.html"), 401
    return render_template("login.html")


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config["LOGIN_RATE_LIMIT"], methods=["POST"])
def register():
    """Public sign-up. Always a viewer: only an admin can grant the admin role."""
    if current_user.is_authenticated:
        return redirect(url_for("pages.index"))
    form = RegisterForm()
    if form.validate_on_submit():
        try:
            create_user(form.username.data, form.password.data, "viewer")
        except ValueError:  # lost a race with another sign-up of the same name
            form.username.errors.append("Потребителското име е заето.")
        else:
            flash("Регистрацията е успешна. Влезте с новия си профил.", "success")
            return redirect(url_for("pages.login"))
    return render_template("register.html", form=form), 400 if form.errors else 200


# Not behind @admin_required on purpose: a viewer must be able to log out.
@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    return redirect(url_for("pages.index"))
