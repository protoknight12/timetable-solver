"""BE-11: an INFEASIBLE result names the user constraints that conflict."""

from dataclasses import replace

from solver import solve
from tests.seeds.test_demo_seeds import seed_of
from tests.solver.test_solver import SOLVED, make
from tests.solver.test_solver_constraints import ROOMS, build, fix, go, hard
from tools.seed_to_contract import seed_to_solver_input

TWO_CLASSES = [([1], [1], [1]), ([1], [2], [1])]  # one teacher, two classes
HARMLESS = hard(9, "max_lessons_per_day", scope="all", max=7)


def test_core_names_the_clashing_fixed_slots_and_nothing_else():
    inp = build(
        make([1], [1, 2], TWO_CLASSES), HARMLESS, fix(1, 1, (1, 1)), fix(2, 2, (1, 1))
    )
    res = go(inp)
    assert res.status == "INFEASIBLE" and res.conflicting_constraint_ids == (1, 2)


def test_core_names_a_hard_room_type_that_no_room_can_meet():
    inp = make([1], [1], [([1], [1], [1], "regular")], rooms=ROOMS, classes=1)
    inp = build(
        inp, HARMLESS, hard(1, "room_type_required", subject_id=1, room_type="lab")
    )
    res = go(inp)
    assert res.status == "INFEASIBLE" and res.conflicting_constraint_ids == (1,)


def test_feasible_result_has_no_conflicts():
    inp = build(make([1], [1, 2], TWO_CLASSES), fix(1, 1, (1, 1)), fix(2, 2, (1, 2)))
    res = go(inp)
    assert res.status in SOLVED and res.conflicting_constraint_ids == ()


def test_impossible_seed_core_is_empty_because_the_data_itself_is_impossible():
    inp = seed_to_solver_input(seed_of("impossible_school.json"))
    assert inp.constraints == ()
    res = solve(inp, time_limit_s=30, num_workers=2)
    assert res.status == "INFEASIBLE" and res.conflicting_constraint_ids == ()
    # A harmless hard constraint must not be blamed for the base-data conflict.
    inp = replace(inp, constraints=(hard(1, "no_gaps", scope="all"),))
    res = solve(inp, time_limit_s=30, num_workers=2)
    assert res.status == "INFEASIBLE" and res.conflicting_constraint_ids == ()
