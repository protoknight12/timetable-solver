from pathlib import Path

import pytest

from checker import check
from solver import solve
from tools.seed_to_contract import load_seed, seed_to_solver_input

SEEDS = Path(__file__).resolve().parents[2] / "seeds"


@pytest.mark.parametrize("name", ["tiny_school.json", "demo_school.json"])
def test_solver_output_has_no_hard_violations(name):
    inp = seed_to_solver_input(load_seed(SEEDS / name))
    res = solve(inp, time_limit_s=30)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert [x for x in check(inp, res) if x.hard] == []


def test_split_class_with_constraints_has_no_hard_violations():
    from tests.checker.helpers import con, course, part, school

    courses = [
        course(1, [1, 1], part(1, [1], [1])),  # whole class
        course(2, [2], part(2, [2], [2]), part(3, [3], [1])),  # parallel groups
        course(4, [1], part(4, [4], [2])),  # other split
    ]
    constraints = [
        con(1, "max_lessons_per_day", {"scope": {"class_id": 1}, "max": 3}),
        con(2, "no_gaps", {"scope": "all"}),
        con(
            3, "fixed_slot", {"course_id": 2, "blocks": [{"day": 1, "start_period": 1}]}
        ),
    ]
    inp = school(courses, constraints, periods=(1, 2, 3, 4))
    res = solve(inp, time_limit_s=30)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert [x for x in check(inp, res) if x.hard] == []
