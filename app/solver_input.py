"""Build the solver's plain-data input from the database (confirmed constraints only)."""

from __future__ import annotations

from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m
from schema import contract as c


def _part_input(p: m.CoursePart) -> c.CoursePart:
    """One course part for the solver.

    BE-20 ASSUMPTION: a subject that requires computers implies that its parts
    need a room (a computer lab cannot be a room-less lesson), so needs_room is
    true for them whatever the part says.

    SUBJ-ROOMS: the same holds for a subject that is tied to rooms. Effective
    allowed rooms of a part: its own ``allowed_room_ids`` when set (the more
    specific setting wins), otherwise the subject's rooms when it has any,
    otherwise any room (None). Room type, computers and seats still apply on
    top: the contract's filters intersect with this list.
    """
    subject_rooms = tuple(sorted(r.id for r in p.subject.rooms))
    needs = p.needs_room or p.subject.requires_computers or bool(subject_rooms)
    allowed = None
    if needs:
        if p.allowed_room_ids is not None:
            allowed = tuple(p.allowed_room_ids)
        elif subject_rooms:
            allowed = subject_rooms
    return c.CoursePart(
        id=p.id,
        subject_id=p.subject_id,
        group_ids=tuple(sorted(g.id for g in p.groups)),
        teacher_ids=tuple(sorted(t.id for t in p.teachers)),
        needs_room=needs,
        # The subject's default room type is resolved here (contract docstring).
        room_type=(p.room_type or p.subject.default_room_type) if needs else None,
        allowed_room_ids=allowed,
    )


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
        parts = tuple(_part_input(p) for p in sorted(course.parts, key=lambda p: p.id))
        courses.append(
            c.Course(
                course.id,
                course.periods_per_week,
                tuple(course.block_sizes),
                parts,
                course.label,
                course.week,
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
        subjects=tuple(c.Subject(s.id, s.requires_computers) for s in rows(m.Subject)),
        classes=tuple(
            c.SchoolClass(k.id, k.name, k.grade, k.student_count)
            for k in rows(m.SchoolClass)
        ),
        groups=tuple(
            c.StudentGroup(
                g.id, g.class_id, g.is_whole, g.name, g.split, g.student_count
            )
            for g in rows(m.StudentGroup)
        ),
        rooms=tuple(
            c.Room(r.id, r.type, r.concurrent_capacity, r.has_computers, r.seats)
            for r in rows(m.Room)
        ),
        courses=tuple(courses),
        constraints=tuple(
            c.Constraint(
                k.id, k.type, k.hard, dict(k.params), None if k.hard else k.weight
            )
            for k in confirmed
        ),
    )
