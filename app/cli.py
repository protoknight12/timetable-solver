"""Flask CLI commands."""

from __future__ import annotations

from datetime import time
from pathlib import Path

import click
from flask.cli import with_appcontext
from sqlalchemy import func, select, text

from app import models as m
from app.auth import create_demo_users
from tools.seed_to_contract import SEED, load_seed, seed_to_solver_input

# Tables that hold school data. `flask seed --reset` empties exactly these.
SCHOOL_MODELS = (
    m.Teacher,
    m.Subject,
    m.SchoolClass,
    m.Room,
    m.Period,
    m.Course,
    m.Constraint,
    m.SolveJob,
)


def _time(value: str) -> time:
    h, minute = value.split(":")
    return time(int(h), int(minute))


def load_school(seed: dict) -> None:
    """Insert the seed into the (empty) database, keeping the converter's ids.

    The ids, splits, room inference and gym capacity all come from
    ``seed_to_solver_input``; here we only add the names it does not carry.
    Seed order and converter order are the same, so ids line up by position.
    """
    inp = seed_to_solver_input(seed)
    s = m.db.session
    s.add_all(
        m.Teacher(id=t.id, name=r["name"])
        for t, r in zip(inp.teachers, seed["teachers"], strict=True)
    )
    s.add_all(
        m.Subject(id=x.id, name=r["name"])
        for x, r in zip(inp.subjects, seed["subjects"], strict=True)
    )
    s.add_all(m.SchoolClass(id=k.id, name=k.name, grade=k.grade) for k in inp.classes)
    s.add_all(
        m.Room(
            id=x.id,
            name=r["name"],
            type=x.type,
            concurrent_capacity=x.concurrent_capacity,
        )
        for x, r in zip(inp.rooms, seed["rooms"], strict=True)
    )
    s.add_all(
        m.Period(
            number=int(p["number"]),
            start_time=_time(p["start"]),
            end_time=_time(p["end"]),
        )
        for p in seed["meta"]["periods"]
    )
    s.flush()  # parents before children
    s.add_all(
        m.StudentGroup(
            id=g.id,
            class_id=g.class_id,
            name=g.name,
            is_whole=g.is_whole,
            split=g.split,
        )
        for g in inp.groups
    )
    s.add_all(
        m.Course(
            id=x.id,
            periods_per_week=x.periods_per_week,
            block_sizes=list(x.block_sizes),
            label=x.label,
        )
        for x in inp.courses
    )
    s.flush()
    for course in inp.courses:
        for p in course.parts:
            s.add(
                m.CoursePart(
                    id=p.id,
                    course_id=course.id,
                    subject_id=p.subject_id,
                    needs_room=p.needs_room,
                    room_type=p.room_type,
                    allowed_room_ids=None
                    if p.allowed_room_ids is None
                    else list(p.allowed_room_ids),
                    groups=[s.get(m.StudentGroup, g) for g in p.group_ids],
                    teachers=[s.get(m.Teacher, t) for t in p.teacher_ids],
                )
            )
    s.flush()
    _reset_sequences()


def _reset_sequences() -> None:
    """PostgreSQL sequences do not know about the explicit ids we inserted."""
    if m.db.engine.dialect.name != "postgresql":
        return
    for model in (
        m.Teacher,
        m.Subject,
        m.SchoolClass,
        m.Room,
        m.StudentGroup,
        m.Course,
        m.CoursePart,
    ):
        t = model.__table__
        m.db.session.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{t.name}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {t.name}), 1))"
            )
        )


def _is_empty() -> bool:
    return all(
        m.db.session.scalar(select(func.count()).select_from(model)) == 0
        for model in SCHOOL_MODELS
    )


@click.command("seed")
@click.argument("path", required=False, type=click.Path(exists=True, dir_okay=False))
@click.option("--reset", is_flag=True, help="Delete all school data first.")
@with_appcontext
def seed_command(path: str | None, reset: bool) -> None:
    """Load the demo school (seeds/example_school.json) into an empty database."""
    if reset:
        for table in reversed(m.db.metadata.sorted_tables):
            if table.name != "users":  # accounts survive a reset
                m.db.session.execute(table.delete())
    elif not _is_empty():
        raise click.ClickException(
            "The database is not empty. Use --reset to replace the data."
        )
    load_school(load_seed(Path(path) if path else SEED))
    m.db.session.commit()
    click.echo("Seeded the demo school.")
    create_demo_users()
