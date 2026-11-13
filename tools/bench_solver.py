"""Benchmark the solver on the real (anonymized) school. Not run in CI.

Run: ``uv run python -m tools.bench_solver [time limits...]`` (default 10 30 60).

Courses that fail ``inp.problems()`` are dropped first and listed, because the
solver refuses broken input. If the result is INFEASIBLE, the script solves
again with one built-in rule removed at a time to show which rule is binding.
This is diagnosis only: the solver itself never relaxes a rule.
"""

from __future__ import annotations

import sys
from dataclasses import replace

import solver
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


def run(inp, rules, limit):
    tm = solver.new_model(inp)
    for rule in rules:
        rule(tm)
    res = solver.run(tm, limit, num_workers=8, random_seed=0)
    return res, len(tm.model.proto.variables), len(tm.model.proto.constraints)


def main(limits):
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
    last = None
    for limit in limits:
        res, nvars, ncons = run(inp, solver.BUILTIN_RULES, limit)
        print(
            f"limit {limit:>3}s: {res.status:<10} wall {res.solve_time_s:6.1f}s  "
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
    main([float(a) for a in sys.argv[1:]] or [10, 30, 60])
