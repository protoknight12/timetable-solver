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
