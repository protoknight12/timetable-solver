"""Alternating A/B weeks in the checker (BE-18, checker assumption A5e)."""

import pytest

from checker import check
from tests.checker.helpers import con, course, lesson, part, school, solved


def keys(courses, lessons, constraints=()):
    got = check(school(courses, constraints), solved(*lessons))
    return sorted((x.rule, x.message_key) for x in got if x.hard)


def one(cid, week, *, group=5, teacher=1, **room):
    """A 1-period course of class 8Б (group 5) by default; part id = course id."""
    return course(cid, [1], part(cid, [group], [teacher], **room), week=week)


TEACHER = [("teacher_conflict", "teacher_double_booked")]
GROUPS = [("group_conflict", "groups_double_booked")]
ROOM = [("room_capacity", "room_over_capacity")]


@pytest.mark.parametrize(
    ("weeks", "expected"),
    [
        (("A", "B"), []),  # different weeks: sharing a slot is fine
        (("A", "A"), TEACHER),
        ((None, "A"), TEACHER),  # an every-week course counts in both weeks
        (("B", None), TEACHER),
        ((None, None), TEACHER),
    ],
)
def test_teacher_shared_between_courses(weeks, expected):
    a, b = (one(1, weeks[0], group=1), one(2, weeks[1], group=5))
    same_slot = [lesson(1, 1, 1, 1), lesson(2, 2, 1, 1)]
    assert keys([a, b], same_slot) == expected


@pytest.mark.parametrize(
    ("weeks", "expected"),
    [(("A", "B"), []), (("A", "A"), GROUPS), ((None, "B"), GROUPS)],
)
def test_group_shared_between_courses(weeks, expected):
    a = course(1, [1], part(1, [1], [1]), week=weeks[0])
    b = course(2, [1], part(2, [1], [2]), week=weeks[1])
    assert keys([a, b], [lesson(1, 1, 1, 1), lesson(2, 2, 1, 1)]) == expected


@pytest.mark.parametrize(
    ("weeks", "expected"),
    [(("A", "B"), []), (("A", "A"), ROOM), ((None, "B"), ROOM)],
)
def test_room_shared_between_courses(weeks, expected):
    lab = {"needs_room": True, "room_type": "lab"}
    a = one(1, weeks[0], group=1, teacher=1, **lab)
    b = one(2, weeks[1], group=5, teacher=2, **lab)
    assert (
        keys([a, b], [lesson(1, 1, 1, 1, 1, 1), lesson(2, 2, 1, 1, 1, 1)]) == expected
    )


def test_room_capacity_two_is_counted_per_week():
    gym = {"needs_room": True, "room_type": "gym"}  # room 3 holds two part-blocks
    cs = [one(1, "A", group=1, **gym), one(2, "A", group=5, teacher=2, **gym)]
    cs.append(course(3, [1], part(3, [4], [2], **gym), week="B"))
    at = [lesson(c, c, 1, 1, 1, 3) for c in (1, 2, 3)]
    # 2 in week A (fine, capacity 2) + 1 in week B
    assert [x for x in keys(cs, at) if x[0] == "room_capacity"] == []
    cs[2] = course(3, [1], part(3, [4], [2], **gym), week=None)
    assert [x[0] for x in keys(cs, at) if x[0] == "room_capacity"] == ["room_capacity"]


def test_gaps_are_judged_per_week():
    no_gaps = [con(1, "no_gaps", {"scope": "all"})]
    a1, a3 = one(1, "A", group=1), one(2, "A", group=1, teacher=2)
    b3 = one(3, "B", group=1, teacher=2)
    gap = [("no_gaps", "gap_in_day")]
    # A has periods 1 and 3 -> a gap in week A.
    assert keys([a1, a3], [lesson(1, 1, 1, 1), lesson(2, 2, 1, 3)], no_gaps) == gap
    # A only at 1, B only at 3: no gap in either week (a union would show one).
    assert keys([a1, b3], [lesson(1, 1, 1, 1), lesson(3, 3, 1, 3)], no_gaps) == []
    # An every-week lesson at 1 plus a B lesson at 3 is a gap in week B only.
    every1 = one(4, None, group=1)
    assert keys([every1, b3], [lesson(4, 4, 1, 1), lesson(3, 3, 1, 3)], no_gaps) == gap


def test_day_start_is_judged_per_week():
    rule = [con(1, "day_starts_first_period", {"scope": "all"})]
    a1, b2 = one(1, "A", group=1), one(2, "B", group=1, teacher=2)
    late = [("day_starts_first_period", "day_starts_late")]
    assert keys([a1, b2], [lesson(1, 1, 1, 1), lesson(2, 2, 1, 2)], rule) == late
    assert keys([a1, b2], [lesson(1, 1, 1, 1), lesson(2, 2, 1, 1)], rule) == []


def test_max_lessons_per_day_is_judged_per_week():
    rule = [con(1, "max_lessons_per_day", {"scope": {"class_id": 1}, "max": 2})]
    cs = [one(1, "A", group=1), one(2, "A", group=1, teacher=2)]
    cs += [one(3, "B", group=1), one(4, "B", group=1, teacher=2)]
    four = [lesson(1, 1, 1, 1), lesson(2, 2, 1, 2), lesson(3, 3, 1, 1)]
    four.append(lesson(4, 4, 1, 2))
    assert keys(cs, four, rule) == []  # 2 per week, though 4 lessons are on the day
    cs.append(one(5, "A", group=1))
    too_many = [("max_lessons_per_day", "too_many_lessons")]
    five = [*four, lesson(5, 5, 1, 3)]
    assert keys(cs, five, rule) == too_many


def test_teacher_max_lessons_per_day_is_judged_per_week():
    rule = [con(1, "teacher_max_lessons_per_day", {"teacher_id": 1, "max": 2})]
    cs = [one(1, "A", group=5), one(2, "A", group=5), one(3, "B", group=5)]
    cs.append(one(4, "B", group=5))
    four = [lesson(1, 1, 1, 1), lesson(2, 2, 1, 2), lesson(3, 3, 1, 1)]
    four.append(lesson(4, 4, 1, 2))
    assert keys(cs, four, rule) == []
    cs.append(one(5, None, group=5))
    got = keys(cs, [*four, lesson(5, 5, 1, 3)], rule)
    assert ("teacher_max_lessons_per_day", "teacher_too_many_lessons") in got


def test_bad_week_value_is_reported_as_input_problem():
    bad = course(1, [1], part(1), week="C")
    assert ("input", "bad_week") in keys([bad], [lesson(1, 1, 1, 1)])
