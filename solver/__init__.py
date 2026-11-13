"""CP-SAT solver core: plain ``SolverInput`` in, plain ``SolveResult`` out.

Implements the built-in hard rules (TECH_STACK.md section 6). Imports only
``schema.contract`` and OR-Tools (CLAUDE.md rules 2 and 3).

Model: one bool per (course, block, day, start period) over the starts where
the block fits. Slot occupancy of a course is a linear sum of those bools, so
the teacher, group and room rules are plain ``<=`` constraints over sums.

User constraints (``inp.constraints``, including ``fixed_slot``) are not
handled yet (BE-07). Each type will get one ``_add_<type>(tm, constraint)``
function that reads ``tm.start`` / ``tm.busy`` and adds its constraints with
``OnlyEnforceIf`` on a per-constraint literal, so BE-11 can pass those
literals as assumptions and read ``sufficient_assumptions_for_infeasibility``.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from schema.contract import ContractError, LessonPlacement, SolveResult, SolverInput

_STATUS = {
    cp_model.OPTIMAL: "OPTIMAL",
    cp_model.FEASIBLE: "FEASIBLE",
    cp_model.INFEASIBLE: "INFEASIBLE",
    cp_model.MODEL_INVALID: "MODEL_INVALID",
    cp_model.UNKNOWN: "UNKNOWN",
}


def _nested_list():
    return defaultdict(list)


@dataclass
class TimetableModel:
    inp: SolverInput
    model: cp_model.CpModel = field(default_factory=cp_model.CpModel)
    # start[(course_id, block_index)][(day, start_period)] -> bool
    start: dict = field(default_factory=dict)
    # busy[course_id][(day, period)] -> literals; at most one is true, so the
    # sum is 1 exactly when the course runs in that slot.
    busy: dict = field(default_factory=lambda: defaultdict(_nested_list))
    # room_start[(part_id, block_index)][room_id][(day, start_period)] -> literal
    room_start: dict = field(default_factory=dict)


def new_model(inp: SolverInput) -> TimetableModel:
    """Block variables, block rules and room assignment (rules 1, 4, 5)."""
    tm = TimetableModel(inp)
    m = tm.model
    last = inp.periods[-1]
    rooms = inp.rooms
    for course in inp.courses:
        per_day = defaultdict(list)
        for b, size in enumerate(course.block_sizes):
            starts = {
                (d, s): m.new_bool_var(f"c{course.id}b{b}d{d}s{s}")
                for d in inp.days
                for s in inp.periods
                if s + size - 1 <= last
            }
            tm.start[course.id, b] = starts
            m.add_exactly_one(starts.values())
            for (d, s), x in starts.items():
                per_day[d].append(x)
                for p in range(s, s + size):
                    tm.busy[course.id][d, p].append(x)
            # Parts share this block's day/start by construction (rule 5); only
            # the room is chosen per part.
            for part in course.parts:
                if not part.needs_room:
                    continue
                cands = [
                    r.id
                    for r in rooms
                    if (part.room_type is None or r.type == part.room_type)
                    and (part.allowed_room_ids is None or r.id in part.allowed_room_ids)
                ]
                if len(cands) == 1:
                    tm.room_start[part.id, b] = {cands[0]: starts}
                    continue
                by_room = {
                    r: {
                        k: m.new_bool_var(f"p{part.id}b{b}r{r}d{k[0]}s{k[1]}")
                        for k in starts
                    }
                    for r in cands
                }
                for k, x in starts.items():
                    m.add(sum(by_room[r][k] for r in cands) == x)
                tm.room_start[part.id, b] = by_room
        # ASSUMPTION (TECH_STACK §15 q10): two blocks of one Course never share a day.
        for lits in per_day.values():
            if len(lits) > 1 and len(course.block_sizes) > 1:
                m.add_at_most_one(lits)
    return tm


def add_teacher_rule(tm: TimetableModel) -> None:
    """Rule 2: a teacher is in at most one part-block per slot."""
    courses_of = defaultdict(set)
    for c in tm.inp.courses:
        for part in c.parts:
            for t in part.teacher_ids:
                courses_of[t].add(c.id)
    for cids in courses_of.values():
        _at_most(tm, sorted(cids), 1)


def add_group_rule(tm: TimetableModel) -> None:
    """Rule 3: per class and slot, whole + any one split group <= 1."""
    groups = {g.id: g for g in tm.inp.groups}
    courses_of = defaultdict(set)
    for c in tm.inp.courses:
        for part in c.parts:
            for gid in part.group_ids:
                courses_of[gid].add(c.id)
    whole = {g.class_id: g.id for g in groups.values() if g.is_whole}
    splits = defaultdict(list)
    for g in groups.values():
        if not g.is_whole:
            splits[g.class_id].append(g.id)
    for class_id, w in whole.items():
        for g in splits[class_id] or [None]:
            cids = courses_of[w] | (courses_of[g] if g is not None else set())
            _at_most(tm, sorted(cids), 1)


def add_room_rule(tm: TimetableModel) -> None:
    """Rule 4: a room holds at most ``concurrent_capacity`` part-blocks per slot."""
    uses = defaultdict(_nested_list)
    sizes = {
        (c.id, b): size for c in tm.inp.courses for b, size in enumerate(c.block_sizes)
    }
    part_course = {p.id: c.id for c in tm.inp.courses for p in c.parts}
    for (pid, b), by_room in tm.room_start.items():
        size = sizes[part_course[pid], b]
        for r, starts in by_room.items():
            for (d, s), lit in starts.items():
                for p in range(s, s + size):
                    uses[r][d, p].append(lit)
    for room in tm.inp.rooms:
        for lits in uses[room.id].values():
            if len(lits) > room.concurrent_capacity:
                tm.model.add(sum(lits) <= room.concurrent_capacity)


BUILTIN_RULES = (add_teacher_rule, add_group_rule, add_room_rule)


def _at_most(tm: TimetableModel, course_ids: list[int], cap: int) -> None:
    if len(course_ids) <= cap:
        return
    slots = {k for cid in course_ids for k in tm.busy[cid]}
    for k in slots:
        lits = [x for cid in course_ids for x in tm.busy[cid].get(k, ())]
        if len(lits) > cap:
            tm.model.add(sum(lits) <= cap)


def run(
    tm: TimetableModel, time_limit_s: float, num_workers: int, random_seed: int
) -> SolveResult:
    t0 = time.perf_counter()
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = num_workers
    solver.parameters.random_seed = random_seed
    code = solver.solve(tm.model)
    status = _STATUS[code]
    lessons = []
    if code in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for course in tm.inp.courses:
            for b, size in enumerate(course.block_sizes):
                (d, s) = next(
                    k for k, x in tm.start[course.id, b].items() if solver.value(x)
                )
                for part in course.parts:
                    room = None
                    if part.needs_room:
                        by_room = tm.room_start[part.id, b]
                        room = next(
                            r for r, st in by_room.items() if solver.value(st[d, s])
                        )
                    lessons.append(
                        LessonPlacement(course.id, part.id, d, s, size, room)
                    )
    return SolveResult(
        status=status,
        lessons=tuple(lessons),
        objective=None,  # no objective until BE-07 adds soft penalties
        solve_time_s=time.perf_counter() - t0,
    )


def solve(
    inp: SolverInput,
    time_limit_s: float = 60,
    num_workers: int = 8,
    random_seed: int = 0,
) -> SolveResult:
    """Build a timetable that satisfies the built-in hard rules."""
    found = inp.problems()
    if found:
        raise ContractError(found)
    tm = new_model(inp)
    for rule in BUILTIN_RULES:
        rule(tm)
    return run(tm, time_limit_s, num_workers, random_seed)
