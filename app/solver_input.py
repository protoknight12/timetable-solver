"""Build the solver's plain-data input from the database (confirmed constraints only)."""

from __future__ import annotations

from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m
from schema import contract as c


def build_solver_input(
    session: Session, days: tuple[int, ...] | None = None
) -> c.SolverInput:
    """Everything is ordered by id, so the result is deterministic.

    Call ``.problems()`` on the result before solving: it reports constraints
    whose ids point at deleted rows, which the database cannot prevent.
    """
    if days is None:
        days = tuple(range(1, current_app.config["SCHOOL_DAYS"] + 1))

    def rows(model, *order):
        return session.scalars(select(model).order_by(*(order or (model.id,)))).all()

    courses = []
    for course in rows(m.Course):
        parts = tuple(
            c.CoursePart(
                id=p.id,
                subject_id=p.subject_id,
                group_ids=tuple(sorted(g.id for g in p.groups)),
                teacher_ids=tuple(sorted(t.id for t in p.teachers)),
                needs_room=p.needs_room,
                # The subject's default room type is resolved here (contract docstring).
                room_type=(p.room_type or p.subject.default_room_type)
                if p.needs_room
                else None,
                allowed_room_ids=None
                if p.allowed_room_ids is None or not p.needs_room
                else tuple(p.allowed_room_ids),
            )
            for p in sorted(course.parts, key=lambda p: p.id)
        )
        courses.append(
            c.Course(
                course.id,
                course.periods_per_week,
                tuple(course.block_sizes),
                parts,
                course.label,
            )
        )

    confirmed = session.scalars(
        select(m.Constraint)
        .where(m.Constraint.status == "confirmed")
        .order_by(m.Constraint.id)
    )
    return c.SolverInput(
        days=days,
        periods=tuple(p.number for p in rows(m.Period, m.Period.number)),
        teachers=tuple(c.Teacher(t.id) for t in rows(m.Teacher)),
        subjects=tuple(c.Subject(s.id) for s in rows(m.Subject)),
        classes=tuple(
            c.SchoolClass(k.id, k.name, k.grade) for k in rows(m.SchoolClass)
        ),
        groups=tuple(
            c.StudentGroup(g.id, g.class_id, g.is_whole, g.name, g.split)
            for g in rows(m.StudentGroup)
        ),
        rooms=tuple(c.Room(r.id, r.type, r.concurrent_capacity) for r in rows(m.Room)),
        courses=tuple(courses),
        constraints=tuple(
            c.Constraint(
                k.id, k.type, k.hard, dict(k.params), None if k.hard else k.weight
            )
            for k in confirmed
        ),
    )
