"""Minimal-change re-solve (BE-13): reference lessons make moves cost, hard rules still win."""

from dataclasses import replace

from checker import check
from schema.contract import Constraint
from solver import count_moved, solve
from tools.seed_to_contract import SEED, load_seed, seed_to_solver_input

SOLVED = ("OPTIMAL", "FEASIBLE")


def _solve(inp, **kw):
    return solve(inp, time_limit_s=10, num_workers=1, **kw)


def _base():
    inp = seed_to_solver_input(load_seed(SEED.parent / "tiny_school.json"))
    first = _solve(inp)
    assert first.status in SOLVED
    return inp, first


def _ban_first_lesson(inp, first, hard=True):
    """A small constraint that bans the slot of the first lesson of its teacher."""
    x = first.lessons[0]
    part = next(p for c in inp.courses for p in c.parts if p.id == x.part_id)
    slots = [
        {"day": x.day, "period": p}
        for p in range(x.start_period, x.start_period + x.length)
    ]
    params = {"teacher_id": part.teacher_ids[0], "slots": slots}
    con = Constraint(99, "teacher_unavailable_slots", hard, params, None if hard else 1)
    return replace(inp, constraints=inp.constraints + (con,))


def test_reference_moves_fewer_lessons_than_a_fresh_solve():
    inp, first = _base()
    changed = _ban_first_lesson(inp, first)
    fresh = _solve(changed, random_seed=7)
    kept = _solve(changed, reference=first.lessons)
    assert fresh.status in SOLVED and kept.status in SOLVED
    moved_fresh = count_moved(first.lessons, fresh.lessons)
    moved_kept = count_moved(first.lessons, kept.lessons)
    assert 1 <= moved_kept < moved_fresh
    assert check(changed, kept) == []  # zero violations, hard or soft
    assert kept.objective == moved_kept  # nothing else is soft here


def test_hard_constraint_still_forces_moves():
    inp, first = _base()
    changed = _ban_first_lesson(inp, first)
    kept = _solve(changed, reference=first.lessons)
    old = first.lessons[0]
    same_part = [x for x in kept.lessons if x.part_id == old.part_id]
    banned = {(s["day"], s["period"]) for s in changed.constraints[-1].params["slots"]}
    # the lesson left its reference slot although staying was free of cost
    assert all(
        (x.day, p) not in banned
        for x in same_part
        for p in range(x.start_period, x.start_period + x.length)
    )
    assert count_moved(first.lessons, kept.lessons) >= 1


def test_unchanged_input_keeps_everything():
    inp, first = _base()
    again = _solve(inp, reference=first.lessons)
    assert count_moved(first.lessons, again.lessons) == 0
    assert again.objective == 0
