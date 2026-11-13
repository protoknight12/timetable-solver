import json
from pathlib import Path

import pytest

from schema.contract import ContractError, SolverInput, load_solver_input
from solver import solve
from tests.solver.verify import violations

EXAMPLE = (
    Path(__file__).resolve().parents[2] / "schema" / "examples" / "split_practice.json"
)
SOLVED = ("OPTIMAL", "FEASIBLE")


@pytest.fixture(scope="module")
def example():
    return load_solver_input(json.loads(EXAMPLE.read_text(encoding="utf-8")))


def make(days, periods, courses, groups=None, rooms=(), classes=2):
    """Tiny input. Class c has whole group c; ``courses`` are (blocks, groups, teachers[, room])."""
    groups = groups or [
        {"id": c, "class_id": c, "is_whole": True} for c in range(1, classes + 1)
    ]
    cs = []
    for i, (blocks, gids, tids, *room) in enumerate(courses, 1):
        part = {"id": i, "subject_id": 1, "group_ids": gids, "teacher_ids": tids}
        if room:
            part |= {"needs_room": True, "room_type": room[0]}
        cs.append(
            {
                "id": i,
                "periods_per_week": sum(blocks),
                "block_sizes": blocks,
                "parts": [part],
            }
        )
    return load_solver_input(
        {
            "days": days,
            "periods": periods,
            "teachers": [{"id": t} for t in range(1, 6)],
            "subjects": [{"id": 1}],
            "classes": [{"id": c, "name": f"{c}"} for c in range(1, classes + 1)],
            "groups": groups,
            "rooms": list(rooms),
            "courses": cs,
        }
    )


def status(inp):
    res = solve(inp, time_limit_s=5, num_workers=1)
    if res.status in SOLVED:
        assert violations(inp, res) == []
    return res.status


def test_example_solves_and_every_rule_holds(example):
    res = solve(example, time_limit_s=10)
    assert res.status in SOLVED
    assert res.objective is None
    assert violations(example, res) == []
    # one placement per part per block
    assert len(res.lessons) == sum(
        len(c.block_sizes) * len(c.parts) for c in example.courses
    )


def test_teacher_double_booked_is_infeasible():
    assert status(make([1], [1], [([1], [1], [1]), ([1], [2], [2])])) in SOLVED
    assert status(make([1], [1], [([1], [1], [1]), ([1], [2], [1])])) == "INFEASIBLE"


SPLIT = [
    {"id": 1, "class_id": 1, "is_whole": True},
    {"id": 2, "class_id": 1, "is_whole": False},
    {"id": 3, "class_id": 1, "is_whole": False},
]


def test_split_groups_of_one_class_may_run_at_once():
    inp = make([1], [1], [([1], [2], [1]), ([1], [3], [2])], groups=SPLIT, classes=1)
    assert status(inp) in SOLVED


def test_split_course_blocks_whole_class():
    inp = make([1], [1], [([1], [1], [1]), ([1], [2], [2])], groups=SPLIT, classes=1)
    assert status(inp) == "INFEASIBLE"


def test_same_split_group_twice_is_infeasible():
    inp = make([1], [1], [([1], [2], [1]), ([1], [2], [2])], groups=SPLIT, classes=1)
    assert status(inp) == "INFEASIBLE"


def test_gym_capacity():
    gym = [{"id": 1, "type": "gym", "concurrent_capacity": 3}]
    pe = [([1], [c], [c], "gym") for c in range(1, 5)]
    assert status(make([1], [1], pe[:3], rooms=gym, classes=4)) in SOLVED
    assert status(make([1], [1], pe, rooms=gym, classes=4)) == "INFEASIBLE"


def test_room_type_candidates_are_respected():
    rooms = [{"id": 1, "type": "regular"}, {"id": 2, "type": "lab"}]
    inp = make(
        [1], [1, 2], [([1], [1], [1], "lab"), ([1], [2], [2], "lab")], rooms=rooms
    )
    res = solve(inp, time_limit_s=5, num_workers=1)
    assert res.status in SOLVED and {les.room_id for les in res.lessons} == {2}
    assert violations(inp, res) == []


def test_more_periods_than_the_week_holds():
    three = [([1], [1], [1]), ([1], [1], [2]), ([1], [1], [3])]
    assert status(make([1], [1, 2, 3], three, classes=1)) in SOLVED
    assert status(make([1], [1, 2], three, classes=1)) == "INFEASIBLE"


def test_blocks_on_one_day_need_a_gap():
    # One day: blocks 2 + 1 fit in 4 periods (with a gap) but not in 3,
    # where they could only touch. status() checks the gap via verify.
    assert status(make([1], [1, 2, 3, 4], [([2, 1], [1], [1])], classes=1)) in SOLVED
    assert status(make([1], [1, 2, 3], [([2, 1], [1], [1])], classes=1)) == "INFEASIBLE"


def test_gap_between_blocks_may_hold_another_course():
    # English 3, Math 2, English 2 on one day of 7 periods (the agreed example).
    courses = [([3, 2], [1], [1]), ([2], [1], [2])]
    assert status(make([1], list(range(1, 8)), courses, classes=1)) in SOLVED


def test_multi_period_block_is_consecutive():
    res = solve(make([1], [1, 2, 3], [([3], [1], [1])], classes=1), time_limit_s=5)
    assert [(les.start_period, les.length) for les in res.lessons] == [(1, 3)]


def test_broken_input_raises():
    inp = make([1], [1], [([1], [1], [1])])
    broken = SolverInput(**{**inp.__dict__, "teachers": ()})
    with pytest.raises(ContractError):
        solve(broken)


def test_same_seed_same_result(example):
    a = solve(example, time_limit_s=10, num_workers=1, random_seed=3)
    b = solve(example, time_limit_s=10, num_workers=1, random_seed=3)
    assert a.lessons == b.lessons
