"""Alternating A/B weeks in the solver (BE-18), cross-checked with the checker."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from checker import check
from schema.contract import load_solver_input
from solver import solve
from tests.checker.helpers import con, course, part, school

EXAMPLE = Path(__file__).resolve().parents[2] / "schema" / "examples" / "ab_weeks.json"
SOLVED = ("OPTIMAL", "FEASIBLE")


def one(cid, week, *, group=1, teacher=1, **room):
    return course(cid, [1], part(cid, [group], [teacher], **room), week=week)


def run(courses, constraints=(), periods=(1,), days=(1,)):
    inp = replace(school(courses, constraints, periods), days=days)
    res = solve(inp, time_limit_s=10, num_workers=1)
    if res.status in SOLVED:
        assert [x for x in check(inp, res) if x.hard] == []
    return res.status in SOLVED


@pytest.mark.parametrize(
    ("weeks", "fits"),
    [
        (("A", "B"), True),
        (("A", "A"), False),
        ((None, "A"), False),
        ((None, "B"), False),
    ],
)
def test_one_slot_teacher_and_group_shared(weeks, fits):
    # One slot in the whole week; same teacher AND same group.
    assert run([one(1, weeks[0]), one(2, weeks[1])]) is fits


@pytest.mark.parametrize(("weeks", "fits"), [(("A", "B"), True), (("A", "A"), False)])
def test_one_slot_room_shared(weeks, fits):
    lab = {"needs_room": True, "room_type": "lab"}
    a = one(1, weeks[0], group=1, teacher=1, **lab)
    b = one(2, weeks[1], group=5, teacher=2, **lab)
    assert run([a, b]) is fits


def fixed(cid, week, period):
    return course(cid, [1], part(cid, [1], [cid % 2 + 1]), week=week), con(
        cid,
        "fixed_slot",
        {"course_id": cid, "blocks": [{"day": 1, "start_period": period}]},
    )


def test_no_gaps_is_per_week():
    no_gaps = con(9, "no_gaps", {"scope": "all"})
    (a1, f1), (b3, f3) = fixed(1, "A", 1), fixed(2, "B", 3)
    assert run([a1, b3], [f1, f3, no_gaps], periods=(1, 2, 3))  # no gap in either week
    (a3, f3a) = fixed(3, "A", 3)
    assert not run([a1, a3], [f1, f3a, no_gaps], periods=(1, 2, 3))  # gap in week A


def test_day_start_is_per_week():
    rule = con(9, "day_starts_first_period", {"scope": "all"})
    (a1, f1), (b2, f2) = fixed(1, "A", 1), fixed(2, "B", 2)
    assert not run([a1, b2], [f1, f2, rule], periods=(1, 2, 3))
    assert run([a1, b2], [f1, rule], periods=(1, 2, 3))  # B is free to start at 1


def test_max_lessons_per_day_is_per_week():
    # One day, two A and two B lessons for one class: 4 on the day, 2 per week.
    cs = [one(i, "A" if i < 3 else "B", teacher=i % 2 + 1) for i in (1, 2, 3, 4)]

    def cap(n):
        return con(9, "max_lessons_per_day", {"scope": {"class_id": 1}, "max": n})

    p = (1, 2, 3, 4)
    assert run(cs, [cap(2)], periods=p)
    assert not run(cs, [cap(1)], periods=p)


def test_teacher_max_per_day_is_per_week():
    cs = [one(i, "A" if i < 3 else "B", group=5) for i in (1, 2, 3, 4)]

    def cap(n):
        return con(9, "teacher_max_lessons_per_day", {"teacher_id": 1, "max": n})

    assert run(cs, [cap(2)], periods=(1, 2, 3, 4))
    assert not run(cs, [cap(1)], periods=(1, 2, 3, 4))


def test_example_school_solves_without_hard_violations():
    inp = load_solver_input(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    res = solve(inp, time_limit_s=10, num_workers=1)
    assert res.status in SOLVED
    assert [x for x in check(inp, res) if x.hard] == []
