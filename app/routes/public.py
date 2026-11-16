"""Public read-only page of one class, reached by a secret link (FE-16). GET only, no login."""

from flask import Blueprint, abort, current_app, render_template
from sqlalchemy import select

from app.auth import limiter
from app.constraint_forms import DAY_NAMES
from app.models import Period, SchoolClass, Subject, db
from app.routes.solve import HAS_TIMETABLE, _grid, published_job
from app.subject_colors import subject_css

bp = Blueprint("public", __name__)


@bp.get("/p/<token>")
@limiter.limit(lambda: current_app.config["PUBLIC_RATE_LIMIT"])
def class_page(token: str):
    # Unknown and switched-off (NULL) tokens look the same: one 404.
    school_class = db.session.scalar(
        select(SchoolClass).where(SchoolClass.public_token == token)
    )
    if school_class is None:
        abort(404)
    # Only the published version: a draft must never leak. Nothing published -> notice page.
    job = published_job()
    if not (job and job.status == "finished" and job.solver_status in HAS_TIMETABLE):
        return render_template("public/class.html", school_class=school_class)
    cells = _grid(job.id, school_class.id)
    used = {e["subject_id"] for lessons in cells.values() for e in lessons}
    return render_template(
        "public/class.html",
        school_class=school_class,
        job=job,
        cells=cells,
        days=range(1, current_app.config["SCHOOL_DAYS"] + 1),
        day_names={d: n.capitalize() for d, n in DAY_NAMES.items()},
        periods=db.session.scalars(select(Period).order_by(Period.number)).all(),
        subject_styles=subject_css(
            db.session.scalars(select(Subject).where(Subject.id.in_(used)))
        ),
    )
