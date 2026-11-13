"""Benchmark the solver on the real (anonymized) school. Not run in CI.

Run: ``uv run python -m tools.bench_solver [--constraints] [time limits...]``
(default 10 30 60). ``--constraints`` adds BENCHMARK constraints to the seed.

Courses that fail ``inp.problems()`` are dropped first and listed, because the
solver refuses broken input. If the result is INFEASIBLE, the script solves
again with one built-in rule removed at a time to show which rule is binding.
This is diagnosis only: the solver itself never relaxes a rule.

The benchmark constraints are values made up to load the solver, NOT school rules.
"""

from __future__ import annotations

import sys
from dataclasses import replace

import solver
from schema.contract import Constraint
from tests.solver.verify import measure
from tools.seed_to_contract import load_seed, seed_to_solver_input


def drop_broken_courses(inp):
    bad = {
        int(p.path.split("[")[1].split("]")[0])
        for p in inp.problems()
        if p.path.startswith("courses[")
    }
    for i in sorted(bad):
        c = inp.courses[i]
        print(f"  dropped course {c.id}: blocks {list(c.block_sizes)}")
    return replace(
        inp, courses=tuple(c for i, c in enumerate(inp.courses) if i not in bad)
    )


def add_benchmark_constraints(inp):
    """BENCHMARK VALUES, not school rules: soft no_gaps "all" (weight 1), four
    courses of different classes pinned to day 1..4 period 1, and five other
    teachers limited to Monday-Thursday.
    """
    parts = {g.id: g.class_id for g in inp.groups}
    pinned, classes = [], set()
    for c in inp.courses:
        cls = {parts[g] for p in c.parts for g in p.group_ids}
        if len(c.block_sizes) == 1 and not cls & classes and len(pinned) < 4:
            pinned.append(c)
            classes |= cls
    busy = {t for c in pinned for p in c.parts for t in p.teacher_ids}
    cons = [Constraint(1, "no_gaps", False, {"scope": "all"}, 1)]
    for i, c in enumerate(pinned):
        blocks = [{"day": i + 1, "start_period": 1}]
        cons.append(
            Constraint(
                len(cons) + 1, "fixed_slot", True, {"course_id": c.id, "blocks": blocks}
            )
        )
    for t in [t.id for t in inp.teachers if t.id not in busy][:5]:
        cons.append(
            Constraint(
                len(cons) + 1,
                "teacher_available_days",
                True,
                {"teacher_id": t, "days": [1, 2, 3, 4]},
            )
        )
    print(f"  benchmark constraints: {len(cons)} (made up, not school rules)")
    return replace(inp, constraints=tuple(cons))


def total_gaps(inp, res):
    """Gaps over all student combinations, by the test-only re-implementation."""
    gaps = Constraint(0, "no_gaps", True, {"scope": "all"})
    return measure(inp, res, gaps) if res.lessons else None


def run(inp, rules, limit):
    tm = solver.new_model(inp)
    for rule in rules:
        rule(tm)
    solver.add_constraints(tm)
    res = solver.run(tm, limit, num_workers=8, random_seed=0)
    return res, len(tm.model.proto.variables), len(tm.model.proto.constraints)


def main(limits, with_constraints=False):
    sys.stdout.reconfigure(encoding="utf-8")
    inp = seed_to_solver_input(load_seed())
    print(
        f"{len(inp.courses)} courses, {sum(c.periods_per_week for c in inp.courses)} periods"
    )
    inp = drop_broken_courses(inp)
    assert not inp.problems(), inp.problems()
    print(
        f"solving {len(inp.courses)} courses, {sum(c.periods_per_week for c in inp.courses)} periods"
    )
    if with_constraints:
        inp = add_benchmark_constraints(inp)
    last = None
    for limit in limits:
        res, nvars, ncons = run(inp, solver.BUILTIN_RULES, limit)
        print(
            f"limit {limit:>3}s: {res.status:<10} wall {res.solve_time_s:6.1f}s  "
            f"objective {res.objective}  gaps {total_gaps(inp, res)}  "
            f"vars {nvars}  constraints {ncons}  lessons {len(res.lessons)}"
        )
        last = res
    if last.status == "INFEASIBLE":
        print("one rule removed at a time:")
        for skip in solver.BUILTIN_RULES:
            rules = [r for r in solver.BUILTIN_RULES if r is not skip]
            res, _, _ = run(inp, rules, limits[-1])
            print(f"  without {skip.__name__:<17}: {res.status}")


if __name__ == "__main__":
    args = sys.argv[1:]
    main(
        [float(a) for a in args if not a.startswith("--")] or [10, 30, 60],
        "--constraints" in args,
    )
