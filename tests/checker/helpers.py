"""Tiny hand-written school for the checker tests.

Grid: days 1-2, periods 1-3.
Class 1 (8А): groups 1 = whole class, 2 and 3 = split "групи", 4 = split "езици".
Class 2 (8Б): group 5 = whole class.
Rooms: 1 = lab, 2 = regular, 3 = gym that holds 2 part-blocks at once.
Teachers 1, 2. Subjects 1, 2.
"""

from checker import Violation
from schema.contract import (
    Constraint,
    Course,
    CoursePart,
    LessonPlacement,
    Room,
    SchoolClass,
    SolveResult,
    SolverInput,
    StudentGroup,
    Subject,
    Teacher,
)

GROUPS = (
    StudentGroup(1, 1, True),
    StudentGroup(2, 1, False, split="групи"),
    StudentGroup(3, 1, False, split="групи"),
    StudentGroup(4, 1, False, split="езици"),
    StudentGroup(5, 2, True),
)
ROOMS = (Room(1, "lab"), Room(2, "regular"), Room(3, "gym", 2))


def school(courses, constraints=(), periods=(1, 2, 3)):
    return SolverInput(
        days=(1, 2),
        periods=periods,
        teachers=(Teacher(1), Teacher(2)),
        subjects=(Subject(1), Subject(2)),
        classes=(SchoolClass(1, "8А", 8), SchoolClass(2, "8Б", 8)),
        groups=GROUPS,
        rooms=ROOMS,
        courses=tuple(courses),
        constraints=tuple(constraints),
    )


def part(pid, groups=(1,), teachers=(1,), subject=1, **room):
    return CoursePart(pid, subject, tuple(groups), tuple(teachers), **room)


def course(cid, blocks, *parts):
    return Course(cid, sum(blocks), tuple(blocks), parts)


def lesson(course_id, part_id, day, start, length=1, room=None):
    return LessonPlacement(course_id, part_id, day, start, length, room)


def solved(*lessons, status="OPTIMAL"):
    return SolveResult(status, tuple(lessons))


def con(cid, type_, params, hard=True, weight=None):
    return Constraint(cid, type_, hard, params, None if hard else weight or 1)


def v(rule, key, *entities, hard=True, cid=None):
    return Violation(rule, hard, key, tuple(entities), cid)
