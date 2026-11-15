"""FE-07: every demo dataset end to end. Seed -> solver -> independent checker."""

import time
from pathlib import Path

import pytest

from checker import check
from solver import solve
from tools.seed_to_contract import load_seed, seed_to_solver_input

SEEDS = Path(__file__).resolve().parents[2] / "seeds"
# Same default as the app (SOLVER_TIME_LIMIT_S); CI must not need more than the jobs get.
TIME_LIMIT_S = 60
FEASIBLE_SEEDS = ["tiny_school.json", "demo_school.json", "example_school.json"]


def solve_seed(name: str):
    inp = seed_to_solver_input(load_seed(SEEDS / name))
    t0 = time.perf_counter()
    res = solve(inp, time_limit_s=TIME_LIMIT_S)
    wall = time.perf_counter() - t0
    # Run with -s (or read the "Captured stdout" of a failure) to see the times.
    print(
        f"[solve-time] {name}: {res.status} wall={wall:.1f}s solver={res.solve_time_s:.1f}s"
    )
    return inp, res, wall


@pytest.mark.parametrize("name", FEASIBLE_SEEDS)
def test_seed_solves_with_zero_hard_violations(name, record_property):
    inp, res, wall = solve_seed(name)
    record_property("solve_time_s", round(wall, 2))
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert wall < TIME_LIMIT_S
    assert [v for v in check(inp, res) if v.hard] == []


def test_impossible_school_is_infeasible():
    _, res, _ = solve_seed("impossible_school.json")
    assert res.status == "INFEASIBLE"
    assert res.lessons == ()
