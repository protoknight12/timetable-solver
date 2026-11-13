"""CP-SAT solver core: plain ``SolverInput`` in, plain ``SolveResult`` out.

Implements the built-in hard rules (TECH_STACK.md section 6). Imports only
``schema.contract`` and OR-Tools (CLAUDE.md rules 2 and 3).

Model: one bool per (course, block, day, start period) over the starts where
the block fits. Slot occupancy of a course is a linear sum of those bools, so
the teacher, group and room rules are plain ``<=`` constraints over sums.

User constraints (``inp.constraints``) get one ``_add_<type>(tm, constraint)``
function each. Every constraint has its own literal in ``tm.enforce``; hard
ones are added with ``OnlyEnforceIf(literal)`` and the literal is fixed to true
in ``_enforce`` (BE-11: delete that one line and pass the literals as
assumptions to read ``sufficient_assumptions_for_infeasibility``). Soft ones
add ``(weight, expression)`` to ``tm.penalties``; ``add_constraints`` sums
them into the one minimised objective.

Assumptions about school rules (TECH_STACK §15 q11 and others) are marked
ASSUMPTION in the comments below.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import product

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
    # constraint id -> literal that is true when the constraint is enforced
    enforce: dict = field(default_factory=dict)
    # (weight, 0/1-or-integer expression) of every soft constraint
    penalties: list = field(default_factory=list)


def new_model(inp: SolverInput) -> TimetableModel:
    """Block variables, block rules and room assignment (rules 1, 4, 5)."""
    tm = TimetableModel(inp)
    m = tm.model
    required = _required_room_types(inp)
    last = inp.periods[-1]
    rooms = inp.rooms
    for course in inp.courses:
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
                for p in range(s, s + size):
                    tm.busy[course.id][d, p].append(x)
            # Parts share this block's day/start by construction (rule 5); only
            # the room is chosen per part.
            for part in course.parts:
                # A hard room_type_required makes the part need a room, of that
                # type as well as of any type it already has (intersection).
                if not (part.needs_room or part.id in required):
                    continue
                cands = [
                    r.id
                    for r in rooms
                    if (part.room_type is None or r.type == part.room_type)
                    and (part.allowed_room_ids is None or r.id in part.allowed_room_ids)
                    and all(r.type == t for t in required.get(part.id, ()))
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
        # Blocks of one Course may share a day and may be back to back; they only
        # must not overlap. No gap rule: it is not a school regulation, so the
        # solver does not invent one (decided 14.11.2026, TECH_STACK §15 q10).
        if len(course.block_sizes) > 1:
            for lits in tm.busy[course.id].values():
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
    """Rule 3: no student is in two lessons at once.

    A student of a class is in the whole-class group plus at most one group of
    each split (TECH_STACK §5, group splits). Groups of the same split are disjoint; groups of
    different splits overlap. So for every combination "one group per split"
    (the cartesian product), the courses of the whole class and of those
    groups may use each slot at most once. A split that does not cover the
    whole class needs no "no group" option: that case is a subset of a
    combination that is already constrained.
    """
    for _, cids in _student_combos(tm):
        _at_most(tm, cids, 1)


def _student_combos(tm: TimetableModel):
    """Yield ``(class_id, sorted course ids)`` for every student combination.

    A combination is the whole class plus one group of each split: what one
    kind of student of the class actually sits through.
    """
    groups = {g.id: g for g in tm.inp.groups}
    courses_of = defaultdict(set)
    for c in tm.inp.courses:
        for part in c.parts:
            for gid in part.group_ids:
                courses_of[gid].add(c.id)
    whole = {g.class_id: g.id for g in groups.values() if g.is_whole}
    splits = defaultdict(lambda: defaultdict(list))  # class -> split -> groups
    for g in groups.values():
        if not g.is_whole:
            splits[g.class_id][g.split].append(g.id)
    for class_id, w in whole.items():
        for combo in product(*splits[class_id].values()):
            cids = set(courses_of[w]).union(*(courses_of[g] for g in combo))
            if cids:
                yield class_id, sorted(cids)


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


# --------------------------------------------------------------------------
# User constraints (BE-07)
# --------------------------------------------------------------------------


def _enforce(tm: TimetableModel, c) -> None:
    lit = tm.model.new_bool_var(f"enforce{c.id}")
    tm.enforce[c.id] = lit
    tm.model.add(lit == 1)  # BE-11: drop this line, pass lit as an assumption


def _limit(tm: TimetableModel, c, expr, cap: int, ub: int) -> None:
    """Hard: ``expr <= cap``. Soft: pay ``weight`` per unit of ``expr`` above ``cap``.

    ``ub`` is the largest value ``expr`` can take.
    """
    if c.hard:
        tm.model.add(expr <= cap).only_enforce_if(tm.enforce[c.id])
    elif cap == 0:
        tm.penalties.append((c.weight, expr))  # expr already counts the violations
    else:
        over = tm.model.new_int_var(0, max(ub - cap, 0), f"over{c.id}")
        tm.model.add(over >= expr - cap)
        tm.penalties.append((c.weight, over))


def _teacher_lits(tm: TimetableModel, teacher_id: int, banned) -> list:
    """Busy literals (one per taught period) in the slots where ``banned(day, period)``."""
    lits = []
    for course in tm.inp.courses:
        if any(teacher_id in p.teacher_ids for p in course.parts):
            for (d, p), ls in tm.busy[course.id].items():
                if banned(d, p):
                    lits += ls
    return lits


def _add_teacher_available_days(tm: TimetableModel, c) -> None:
    # ASSUMPTION: no lesson of the teacher on any other day; soft pays per period
    # taught on another day (a 2-period block on a bad day costs 2).
    days = set(c.params["days"])
    lits = _teacher_lits(tm, c.params["teacher_id"], lambda d, p: d not in days)
    if lits:
        _limit(tm, c, cp_model.LinearExpr.sum(lits), 0, len(lits))


def _add_teacher_unavailable_slots(tm: TimetableModel, c) -> None:
    # Soft pays per taught period inside the listed slots.
    slots = {(s["day"], s["period"]) for s in c.params["slots"]}
    lits = _teacher_lits(tm, c.params["teacher_id"], lambda d, p: (d, p) in slots)
    if lits:
        _limit(tm, c, cp_model.LinearExpr.sum(lits), 0, len(lits))


def _matching_parts(inp: SolverInput, params: dict) -> set[int]:
    return {
        p.id
        for c in inp.courses
        for p in c.parts
        if c.id == params.get("course_id") or p.subject_id == params.get("subject_id")
    }


def _required_room_types(inp: SolverInput) -> dict[int, set[str]]:
    """Part id -> room types demanded by hard ``room_type_required`` constraints."""
    out = defaultdict(set)
    for c in inp.constraints:
        if c.type == "room_type_required" and c.hard:
            for pid in _matching_parts(inp, c.params):
                out[pid].add(c.params["room_type"])
    return out


def _add_room_type_required(tm: TimetableModel, c) -> None:
    # Hard is structural: ``new_model`` shrinks the candidate rooms of the
    # matching parts (and makes them need a room), so there is nothing to add
    # here. No candidate left -> INFEASIBLE. This part is not tied to the
    # enforcement literal; BE-11 must report it from the constraint itself.
    # Soft is a plain sum over room literals that already exist: one unit per
    # part-block placed in a room of another type. Parts that need no room are
    # not touched (ASSUMPTION: "prefer lab" never creates a room demand).
    if c.hard:
        return
    parts = _matching_parts(tm.inp, c.params)
    type_of = {r.id: r.type for r in tm.inp.rooms}
    lits = [
        lit
        for (pid, _), by_room in tm.room_start.items()
        if pid in parts
        for r, starts in by_room.items()
        if type_of[r] != c.params["room_type"]
        for lit in starts.values()
    ]
    if lits:
        _limit(tm, c, cp_model.LinearExpr.sum(lits), 0, len(lits))


def _scope_combos(tm: TimetableModel, scope):
    grade_of = {k.id: k.grade for k in tm.inp.classes}
    for class_id, cids in _student_combos(tm):
        if (
            scope == "all"
            or scope.get("class_id") == class_id
            or ("grade" in scope and grade_of[class_id] == scope["grade"])
        ):
            yield cids


def _add_max_lessons_per_day(tm: TimetableModel, c) -> None:
    # ASSUMPTION: "lessons" are periods (a 2-period block counts 2), counted per
    # student combination, so a pupil of a split class is not charged for the
    # lessons of the other group. Soft pays per period over the maximum.
    cap = c.params["max"]
    for cids in _scope_combos(tm, c.params["scope"]):
        for d in tm.inp.days:
            lits = [
                x
                for cid in cids
                for p in tm.inp.periods
                for x in tm.busy[cid].get((d, p), ())
            ]
            if len(lits) > cap:
                expr = cp_model.LinearExpr.sum(lits)
                _limit(tm, c, expr, cap, len(tm.inp.periods))


def _reach(m, occ, seq, name):
    """flag[p]: forced true when the student has a lesson before p (in ``seq`` order).

    May also be true otherwise; that only makes the gap test stricter, and the
    solver sets it to the truth when that is cheaper, so it is never unsound.
    None means false.
    """
    flag, prev = {}, None
    for p in seq:
        flag[p] = prev
        if occ[p] or prev is not None:
            nxt = m.new_bool_var(f"{name}p{p}")
            if occ[p]:
                m.add(nxt >= cp_model.LinearExpr.sum(occ[p]))
            if prev is not None:
                m.add(nxt >= prev)
            prev = nxt
    return flag


def _add_no_gaps(tm: TimetableModel, c) -> None:
    # ASSUMPTION (TECH_STACK §15 q11): a gap is a free period with a lesson
    # before and after it on the same day, for one student combination.
    # Free periods at the start or end of the day are not gaps. Soft pays
    # ``weight`` per gap, so each combination of a split class counts on its own.
    m, periods = tm.model, tm.inp.periods
    for cids in _scope_combos(tm, c.params["scope"]):
        for d in tm.inp.days:
            occ = {
                p: [x for cid in cids for x in tm.busy[cid].get((d, p), ())]
                for p in periods
            }
            before = _reach(m, occ, periods, f"before{c.id}d{d}")
            after = _reach(m, occ, periods[::-1], f"after{c.id}d{d}")
            for p in periods[1:-1]:
                if before[p] is not None and after[p] is not None:
                    expr = before[p] + after[p] - cp_model.LinearExpr.sum(occ[p])
                    _limit(tm, c, expr, 1, 2)


def _add_fixed_slot(tm: TimetableModel, c) -> None:
    # Block i starts at blocks[i]. The contract already checked it fits the day.
    # Soft pays per block that is somewhere else.
    cid = c.params["course_id"]
    moved = [
        1 - tm.start[cid, i][b["day"], b["start_period"]]
        for i, b in enumerate(c.params["blocks"])
    ]
    _limit(tm, c, cp_model.LinearExpr.sum(moved), 0, len(moved))


_ADDERS = {
    "teacher_available_days": _add_teacher_available_days,
    "teacher_unavailable_slots": _add_teacher_unavailable_slots,
    "room_type_required": _add_room_type_required,
    "max_lessons_per_day": _add_max_lessons_per_day,
    "no_gaps": _add_no_gaps,
    "fixed_slot": _add_fixed_slot,
}


def add_constraints(tm: TimetableModel) -> None:
    """Add every user constraint, then one objective from the soft ones."""
    for c in tm.inp.constraints:
        _enforce(tm, c)
        _ADDERS[c.type](tm, c)
    if tm.penalties:
        tm.model.minimize(sum(w * e for w, e in tm.penalties))


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
    soft = any(not c.hard for c in tm.inp.constraints)
    if code in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for course in tm.inp.courses:
            for b, size in enumerate(course.block_sizes):
                (d, s) = next(
                    k for k, x in tm.start[course.id, b].items() if solver.value(x)
                )
                for part in course.parts:
                    room = None
                    if (part.id, b) in tm.room_start:
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
        # Reported value of the minimised sum; None when nothing is soft.
        objective=round(solver.objective_value) if lessons and soft else None,
        solve_time_s=time.perf_counter() - t0,
    )


def solve(
    inp: SolverInput,
    time_limit_s: float = 60,
    num_workers: int = 8,
    random_seed: int = 0,
) -> SolveResult:
    """Build a timetable that satisfies the built-in rules and the constraints."""
    found = inp.problems()
    if found:
        raise ContractError(found)
    tm = new_model(inp)
    for rule in BUILTIN_RULES:
        rule(tm)
    add_constraints(tm)
    return run(tm, time_limit_s, num_workers, random_seed)
