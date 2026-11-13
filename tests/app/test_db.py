import dataclasses
from datetime import time

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from app import models as m
from app.cli import load_school, seed_command
from app.models import db
from app.solver_input import build_solver_input
from tools.seed_to_contract import load_seed, seed_to_solver_input


def test_migration_upgrade_and_downgrade(app):
    upgrade(directory="migrations")
    tables = set(inspect(db.engine).get_table_names())
    assert {"teachers", "course_parts", "lessons", "constraints"} <= tables
    downgrade(directory="migrations", revision="base")
    assert set(inspect(db.engine).get_table_names()) <= {"alembic_version"}


def _count(model):
    return db.session.scalar(select(func.count()).select_from(model))


def test_seed_counts_and_structure(migrated):
    seed = load_seed()
    load_school(seed)
    db.session.commit()
    assert _count(m.Teacher) == len(seed["teachers"])
    assert _count(m.SchoolClass) == len(seed["classes"])
    assert _count(m.Room) == len(seed["rooms"])
    assert _count(m.Course) == len(seed["curriculum"])
    assert _count(m.Period) == len(seed["meta"]["periods"])
    whole = db.session.scalars(select(m.StudentGroup).where(m.StudentGroup.is_whole))
    assert sorted(g.class_id for g in whole) == sorted(
        c.id for c in db.session.scalars(select(m.SchoolClass))
    )
    splits = set(db.session.scalars(select(m.StudentGroup.split)))
    assert splits == {"", "групи", "езици"}


def test_second_whole_group_is_rejected(migrated):
    load_school(load_seed())
    db.session.commit()
    db.session.add(m.StudentGroup(class_id=1, name="още един", is_whole=True))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_deleting_a_class_removes_its_groups(migrated):
    load_school(load_seed())
    db.session.commit()
    db.session.delete(db.session.get(m.SchoolClass, 1))
    db.session.commit()
    assert (
        db.session.scalar(select(func.count()).where(m.StudentGroup.class_id == 1)) == 0
    )


def _normalised(inp):
    """Group and teacher ids of a part have no order in the database."""
    courses = tuple(
        dataclasses.replace(
            c,
            parts=tuple(
                dataclasses.replace(
                    p,
                    group_ids=tuple(sorted(p.group_ids)),
                    teacher_ids=tuple(sorted(p.teacher_ids)),
                )
                for p in c.parts
            ),
        )
        for c in inp.courses
    )
    return dataclasses.replace(inp, courses=courses)


def test_round_trip_seed_to_db_to_solver_input(migrated):
    seed = load_seed()
    load_school(seed)
    db.session.commit()
    from_db = build_solver_input(db.session)
    assert _normalised(from_db) == _normalised(seed_to_solver_input(seed))
    assert from_db.problems() == []


def test_deleted_teacher_is_reported(migrated):
    db.session.add_all([m.Teacher(id=1, name="А"), m.Teacher(id=2, name="Б")])
    db.session.add(
        m.Constraint(
            type="teacher_available_days",
            params={"teacher_id": 2, "days": [5]},
            status="confirmed",
        )
    )
    db.session.add(
        m.Period(
            number=1,
            start_time=time(8),
            end_time=time(9),
        )
    )
    db.session.commit()
    assert build_solver_input(db.session).problems() == []
    db.session.delete(db.session.get(m.Teacher, 2))
    db.session.commit()
    problems = build_solver_input(db.session).problems()
    assert [p.code for p in problems] == ["unknown_reference"]
    assert "teacher_id" in problems[0].path


def test_only_confirmed_constraints_reach_the_solver(migrated):
    for status in ("proposed", "confirmed", "rejected"):
        db.session.add(
            m.Constraint(type="no_gaps", params={}, status=status, hard=True)
        )
    db.session.commit()
    assert [c.id for c in build_solver_input(db.session).constraints] == [2]


def test_seed_command_refuses_non_empty_db_without_reset(migrated):
    runner = migrated.test_cli_runner()
    assert runner.invoke(seed_command).exit_code == 0
    again = runner.invoke(seed_command)
    assert again.exit_code != 0 and "--reset" in again.output
    assert runner.invoke(seed_command, ["--reset"]).exit_code == 0
    assert _count(m.Course) == len(load_seed()["curriculum"])


def test_new_rows_get_fresh_ids_after_seeding(migrated):
    # On PostgreSQL this fails unless the sequences were moved past the seeded ids.
    load_school(load_seed())
    db.session.commit()
    t = m.Teacher(name="Нов")
    db.session.add(t)
    db.session.commit()
    assert t.id == _count(m.Teacher)
