"""Read-only REST API (JSON) for the published timetable.

Fits the tournament sub-category "Уеб услуги & APIs": other systems (a school
website, an e-diary, a hallway display, a calendar script) can read the timetable
without scraping HTML. GET only, so nothing here can change data.

Access:
- a logged-in session (any role), or
- ``Authorization: Bearer <API_TOKEN>`` when the server sets ``API_TOKEN``, or
- ``/api/v1/public/<token>`` with a class's existing public link token (one class only).

Only the *published* timetable is ever returned, so a draft never leaks.
ASSUMPTION: one installation-wide API token is enough (one school per installation);
per-client keys would need a new table (rule 7) and are left for later.
"""

from __future__ import annotations

import hmac

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.auth import limiter
from app.constraint_forms import DAY_NAMES
from app.models import (
    CoursePart,
    Lesson,
    Period,
    Room,
    SchoolClass,
    Subject,
    Teacher,
    db,
)
from app.routes.solve import HAS_TIMETABLE, published_job

bp = Blueprint("api", __name__, url_prefix="/api/v1")

API_VERSION = "1.0"

ERRORS = {
    400: "Невалидна заявка.",
    401: "Нужен е вход в системата или валиден API ключ.",
    403: "Нямате достъп до този ресурс.",
    404: "Ресурсът не е намерен.",
    405: "Методът не е разрешен. API-то е само за четене.",
    413: "Заявката е твърде голяма.",
    429: "Твърде много заявки. Изчакайте малко и опитайте отново.",
    500: "Възникна грешка от наша страна.",
}


class ApiError(Exception):
    def __init__(self, code: int, message: str | None = None):
        self.code, self.message = code, message or ERRORS[code]


def _error(code: int, message: str | None = None):
    response = jsonify(error={"code": code, "message": message or ERRORS[code]})
    response.status_code = code
    if code == 401:
        response.headers["WWW-Authenticate"] = 'Bearer realm="saturn"'
    return response


@bp.errorhandler(ApiError)
def _api_error(e: ApiError):
    return _error(e.code, e.message)


# JSON instead of the HTML error pages for everything under /api/v1.
for _code in (404, 405, 429, 500):
    bp.register_error_handler(_code, lambda e, c=_code: _error(c))


def _rate_key() -> str:
    # Logged-in users get their own bucket; everyone else is counted per IP.
    if current_user.is_authenticated:
        return f"user:{current_user.get_id()}"
    return f"ip:{request.remote_addr}"


api_limit = limiter.shared_limit(
    lambda: current_app.config["API_RATE_LIMIT"], scope="api", key_func=_rate_key
)


def _bearer_ok() -> bool:
    expected = current_app.config.get("API_TOKEN") or ""
    header = request.headers.get("Authorization", "")
    if not expected or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:].strip().encode(), expected.encode())


def _require_access() -> None:
    if not (current_user.is_authenticated or _bearer_ok()):
        raise ApiError(401)


@bp.after_request
def _no_store(response):
    # Timetables change on publish; clients must not keep a stale copy.
    response.headers["Cache-Control"] = "no-store"
    return response


def _published():
    job = published_job()
    if not (job and job.status == "finished" and job.solver_status in HAS_TIMETABLE):
        raise ApiError(404, "Няма публикувано разписание.")
    return job


def _int_arg(name: str) -> int | None:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        raise ApiError(404, f"Параметърът „{name}“ трябва да е цяло число.") from None


def _periods() -> list[dict]:
    return [
        {
            "number": p.number,
            "start": p.start_time.strftime("%H:%M"),
            "end": p.end_time.strftime("%H:%M"),
        }
        for p in db.session.scalars(select(Period).order_by(Period.number))
    ]


def _days() -> list[dict]:
    return [
        {"number": d, "name": DAY_NAMES[d].capitalize()}
        for d in range(1, current_app.config["SCHOOL_DAYS"] + 1)
    ]


def _lessons(
    job_id: int,
    class_id: int | None = None,
    teacher_id: int | None = None,
    room_id: int | None = None,
    day: int | None = None,
) -> list[dict]:
    """Every placed block of the job as plain JSON, optionally filtered."""
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
    classes = {c.id: c.name for c in db.session.scalars(select(SchoolClass))}
    rooms = {r.id: r.name for r in db.session.scalars(select(Room))}
    query = select(Lesson).where(Lesson.job_id == job_id)
    if day is not None:
        query = query.where(Lesson.day == day)
    if room_id is not None:
        query = query.where(Lesson.room_id == room_id)
    out = []
    for les in db.session.scalars(
        query.order_by(Lesson.day, Lesson.start_period, Lesson.id)
    ):
        part = parts[les.part_id]
        if class_id is not None and class_id not in {g.class_id for g in part.groups}:
            continue
        if teacher_id is not None and teacher_id not in {t.id for t in part.teachers}:
            continue
        out.append(
            {
                "id": les.id,
                "day": les.day,
                "start_period": les.start_period,
                "length": les.length,
                # None = every week; "A"/"B" = alternating weeks.
                "week": part.course.week,
                "subject": {"id": part.subject.id, "name": part.subject.name},
                "teachers": [
                    {"id": t.id, "name": t.name}
                    for t in sorted(part.teachers, key=lambda t: t.id)
                ],
                "groups": [
                    {
                        "id": g.id,
                        "class_id": g.class_id,
                        "class_name": classes.get(g.class_id, ""),
                        "name": g.name,
                        "whole_class": g.is_whole,
                    }
                    for g in sorted(part.groups, key=lambda g: g.id)
                    # A class view shows only that class's groups of a joint lesson.
                    if class_id is None or g.class_id == class_id
                ],
                "room": (
                    {"id": les.room_id, "name": rooms.get(les.room_id, "")}
                    if les.room_id is not None
                    else None
                ),
            }
        )
    return out


def _timetable(job, lessons: list[dict]) -> dict:
    return {
        "timetable": {
            "job_id": job.id,
            "solver_status": job.solver_status,
            "published_at": job.published_at.isoformat(),
        },
        "days": _days(),
        "periods": _periods(),
        "lessons": lessons,
    }


def _get_or_404(model, ident: int):
    obj = db.session.get(model, ident)
    if obj is None:
        raise ApiError(404)
    return obj


@bp.get("")
@bp.get("/")
@api_limit
def index():
    """Entry point: version and links, so a client can discover the API."""
    return jsonify(
        name="SATurn API",
        version=API_VERSION,
        read_only=True,
        links={
            "openapi": "/api/v1/openapi.json",
            "timetable": "/api/v1/timetable",
            "classes": "/api/v1/classes",
            "teachers": "/api/v1/teachers",
            "rooms": "/api/v1/rooms",
            "subjects": "/api/v1/subjects",
            "periods": "/api/v1/periods",
            "public_class": "/api/v1/public/{token}",
        },
    )


@bp.get("/openapi.json")
@api_limit
def openapi():
    from app.api_spec import spec

    return jsonify(spec())


@bp.get("/timetable")
@api_limit
def timetable():
    _require_access()
    job = _published()
    class_id, teacher_id = _int_arg("class_id"), _int_arg("teacher_id")
    room_id, day = _int_arg("room_id"), _int_arg("day")
    return jsonify(
        _timetable(job, _lessons(job.id, class_id, teacher_id, room_id, day))
    )


@bp.get("/classes")
@api_limit
def classes():
    _require_access()
    rows = db.session.scalars(
        select(SchoolClass).order_by(SchoolClass.grade, SchoolClass.name)
    )
    return jsonify(
        classes=[
            {
                "id": c.id,
                "name": c.name,
                "grade": c.grade,
                "student_count": c.student_count,
                "class_teacher_id": c.class_teacher_id,
            }
            for c in rows
        ]
    )


@bp.get("/classes/<int:class_id>/timetable")
@api_limit
def class_timetable(class_id: int):
    _require_access()
    school_class = _get_or_404(SchoolClass, class_id)
    job = _published()
    data = _timetable(job, _lessons(job.id, class_id=class_id, day=_int_arg("day")))
    data["class"] = {"id": school_class.id, "name": school_class.name}
    return jsonify(data)


@bp.get("/teachers")
@api_limit
def teachers():
    _require_access()
    rows = db.session.scalars(select(Teacher).order_by(Teacher.name))
    return jsonify(
        teachers=[
            {"id": t.id, "name": t.name, "short_name": t.short_name} for t in rows
        ]
    )


@bp.get("/teachers/<int:teacher_id>/timetable")
@api_limit
def teacher_timetable(teacher_id: int):
    _require_access()
    teacher = _get_or_404(Teacher, teacher_id)
    job = _published()
    data = _timetable(job, _lessons(job.id, teacher_id=teacher_id, day=_int_arg("day")))
    data["teacher"] = {"id": teacher.id, "name": teacher.name}
    return jsonify(data)


@bp.get("/rooms")
@api_limit
def rooms():
    _require_access()
    rows = db.session.scalars(select(Room).order_by(Room.name))
    return jsonify(
        rooms=[
            {"id": r.id, "name": r.name, "type": r.type, "seats": r.seats} for r in rows
        ]
    )


@bp.get("/subjects")
@api_limit
def subjects():
    _require_access()
    rows = db.session.scalars(select(Subject).order_by(Subject.name))
    return jsonify(
        subjects=[
            {"id": s.id, "name": s.name, "short_name": s.short_name} for s in rows
        ]
    )


@bp.get("/periods")
@api_limit
def periods():
    _require_access()
    return jsonify(days=_days(), periods=_periods())


@bp.get("/public/<token>")
@limiter.limit(lambda: current_app.config["PUBLIC_RATE_LIMIT"])
def public_class(token: str):
    """One class, no login: the JSON twin of the public page /p/<token>."""
    school_class = db.session.scalar(
        select(SchoolClass).where(SchoolClass.public_token == token)
    )
    if school_class is None:
        # Unknown and switched-off tokens look the same, as on /p/<token>.
        raise ApiError(404)
    job = _published()
    data = _timetable(job, _lessons(job.id, class_id=school_class.id))
    data["class"] = {"id": school_class.id, "name": school_class.name}
    return jsonify(data)
