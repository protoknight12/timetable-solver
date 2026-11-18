"""Background solve job (BE-08): build input, solve, check, save.

The RQ worker has no Flask app, so ``run_solve`` makes one. It never lets an
exception escape with the job still "running".
"""

from __future__ import annotations

import logging
import re
import threading

from flask import current_app, has_app_context
from sqlalchemy import func, select

from app import create_app
from app.models import (
    Lesson,
    SchoolClass,
    SolveJob,
    StudentGroup,
    Subject,
    Violation,
    db,
)
from app.solver_input import build_solver_input
from checker import check
from schema.contract import LessonPlacement
from solver import solve

log = logging.getLogger(__name__)

ERROR_UNEXPECTED = "Неочаквана грешка при изграждането на графика."
ERROR_INPUT = (
    "Входните данни не са пълни или съдържат грешки (например изтрити "
    "записи или пропуск в учебните часове). "
    "Прегледайте данните и ограниченията."
)
_PART_PATH = re.compile(r"^courses\[(\d+)\]\.parts\[(\d+)\]")
_MAX_LISTED = 5


def input_error(inp, problems) -> str:
    """Bulgarian error for a job that cannot start (BE-20).

    "No matching room" became common with the computer and seat rules, so those
    parts are named (subject and classes) instead of the generic message.
    """
    names = []
    for p in problems:
        m = _PART_PATH.match(p.path) if p.code == "no_matching_room" else None
        if not m:
            continue
        part = inp.courses[int(m[1])].parts[int(m[2])]
        subject = db.session.get(Subject, part.subject_id)
        classes = sorted(
            {
                k.name
                for g in db.session.scalars(
                    select(StudentGroup).where(StudentGroup.id.in_(part.group_ids))
                )
                if (k := db.session.get(SchoolClass, g.class_id))
            }
        )
        label = subject.name if subject else f"предмет №{part.subject_id}"
        names.append(f"{label} ({', '.join(classes)})" if classes else label)
    if not names:
        return ERROR_INPUT
    unique = list(dict.fromkeys(names))
    shown = "; ".join(unique[:_MAX_LISTED])
    if len(unique) > _MAX_LISTED:
        shown += f" и още {len(unique) - _MAX_LISTED}"
    return (
        f"Няма подходяща стая за: {shown}. Проверете типа стая, компютрите и "
        "местата в стаите и броя ученици в класовете и групите."
    )


def run_solve(job_id: int) -> None:
    if has_app_context():  # tests, or any caller that already has an app
        _run(job_id)
    else:
        with create_app().app_context():
            _run(job_id)


def solve_in_thread(job_id: int) -> threading.Thread:
    """Run the job in a thread of this process (SOLVE_INLINE, local dev only).

    For machines without Redis or an RQ worker (the worker cannot fork on
    Windows). Production keeps RQ: a thread dies with the web process and
    every gunicorn worker would solve on its own CPU budget.
    """
    app = current_app._get_current_object()

    def target() -> None:
        with app.app_context():
            _run(job_id)

    thread = threading.Thread(target=target, name=f"solve-{job_id}", daemon=True)
    thread.start()
    return thread


def placements(job_id: int | None) -> list[LessonPlacement]:
    """Saved lessons of a job as plain solver data (empty for no job)."""
    rows = db.session.scalars(select(Lesson).where(Lesson.job_id == job_id))
    return [
        LessonPlacement(
            x.course_id, x.part_id, x.day, x.start_period, x.length, x.room_id
        )
        for x in rows
    ]


def _fail(job: SolveJob, error: str) -> None:
    job.status, job.error, job.finished_at = "failed", error, func.now()
    db.session.commit()


def _run(job_id: int) -> None:
    job = db.session.get(SolveJob, job_id)
    if job is None:
        return
    try:
        job.status, job.started_at = "running", func.now()
        db.session.commit()
        inp = build_solver_input(db.session)
        problems = inp.problems()
        if problems:
            # Never solve with broken input (deleted records, a gap in the periods...).
            log.warning(
                "solve job %s: input problems: %s",
                job_id,
                "; ".join(f"{p.code} {p.path}" for p in problems),
            )
            _fail(job, input_error(inp, problems))
            return
        limit = job.time_limit_s or current_app.config["SOLVER_TIME_LIMIT_S"]
        # base_job_id set = keep as much of that job's timetable as possible (BE-13).
        result = solve(inp, time_limit_s=limit, reference=placements(job.base_job_id))
        violations = check(inp, result)
        db.session.add_all(
            Lesson(
                job_id=job.id,
                course_id=x.course_id,
                part_id=x.part_id,
                day=x.day,
                start_period=x.start_period,
                length=x.length,
                room_id=x.room_id,
            )
            for x in result.lessons
        )
        db.session.add_all(
            Violation(
                job_id=job.id,
                type=v.rule,
                entity_ids=[list(e) for e in v.entities],
                message_key=v.message_key,
                hard=v.hard,
                constraint_id=v.constraint_id,
            )
            for v in violations
        )
        # Conflicting constraints reuse the violations table: constraint_id has no FK.
        db.session.add_all(
            Violation(
                job_id=job.id,
                type="infeasible_core",
                entity_ids=[],
                message_key="infeasible_core",
                hard=True,
                constraint_id=cid,
            )
            for cid in result.conflicting_constraint_ids
        )
        job.solver_status, job.objective = result.status, result.objective
        job.status, job.finished_at = "finished", func.now()
        db.session.commit()
    except Exception:
        log.exception("solve job %s failed", job_id)
        db.session.rollback()
        _fail(db.session.get(SolveJob, job_id), ERROR_UNEXPECTED)
