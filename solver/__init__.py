"""CP-SAT solver core: plain ``SolverInput`` in, plain ``SolveResult`` out.

Implements the built-in hard rules (TECH_STACK.md section 6). Imports only
``schema.contract`` and OR-Tools (CLAUDE.md rules 2 and 3).

Model: one bool per (course, block, day, start period) over the starts where
the block fits. Slot occupancy of a course is a linear sum of those bools, so
the teacher, group and room rules are plain ``<=`` constraints over sums.

User constraints (``inp.constraints``) get one ``_add_<type>(tm, constraint)``
function each. Every constraint has its own literal in ``tm.enforce``; hard
ones are added with ``OnlyEnforceIf(literal)`` and the literal is fixed to true
in ``_enforce``. Only when that solve is INFEASIBLE, ``solve`` builds the model
again in explain mode (BE-11): the literals of the hard constraints become
assumptions and ``sufficient_assumptions_for_infeasibility`` names the
conflicting constraints. The normal solve path is not touched. Soft ones
add ``(weight, expression)`` to ``tm.penalties``; ``add_constraints`` sums
them into the one minimised objective.

Minimal-change re-solve (BE-13): ``solve(..., reference=previous lessons)`` adds
one soft penalty (``MOVE_WEIGHT``) per block that leaves its previous day and
start period. It is only another term of the objective, so hard rules always
win; courses missing from the reference are free. Without a reference nothing
changes. The reference is a separate argument, not part of ``SolverInput``.

Alternating weeks (BE-18, rule in ``schema.contract``): every per-slot "at most"
rule and every per-day constraint is added once for week A and once for week B;
a course with ``week=None`` counts in both, an A course only in A. Soft
penalties of the two weeks add up. If no course has a week, nothing is
duplicated (``tm.weekly`` is false and the weeks collapse to one pass).

Assumptions about school rules (TECH_STACK §15 q11 and others) are marked
ASSUMPTION in the comments below.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from itertools import product

from ortools.sat.python import cp_model

from schema.contract import (
    WEEKS,
    ContractError,
    LessonPlacement,
    SolveResult,
    SolverInput,
)

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
    # Explain mode (BE-11): hard constraints are assumptions, not fixed to true.
    explain: bool = False
    # Previous lessons for the minimal-change re-solve (BE-13); empty = fresh solve.
    reference: tuple = ()
    # course id -> "A" / "B" / None, and whether any course has a week (BE-18).
    week_of: dict = field(default_factory=dict)
    weekly: bool = False


def _passes(tm: TimetableModel) -> tuple:
    """The weeks a rule is evaluated for; ``(None,)`` = all courses, one pass."""
    return WEEKS if tm.weekly else (None,)


def _in_week(tm: TimetableModel, course_ids, week) -> list:
    """The courses that run in ``week`` (every-week courses run in both)."""
    return [c for c in course_ids if week is None or tm.week_of[c] in (None, week)]


def new_model(inp: SolverInput, explain: bool = False) -> TimetableModel:
    """Block variables, block rules and room assignment (rules 1, 4, 5)."""
    tm = TimetableModel(inp, explain=explain)
    tm.week_of = {c.id: c.week for c in inp.courses}
    tm.weekly = any(c.week for c in inp.courses)
    m = tm.model
    needed = _required_room_types(inp)
    # In explain mode the room types are enforced by the constraint's literal
    # (``_add_room_type_required``), not by shrinking the candidate rooms.
    required = {} if explain else needed
    last = inp.periods[-1]
    rooms = inp.rooms
    computer_subjects = {x.id for x in inp.subjects if x.requires_computers}
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
                # A subject that requires computers makes its parts need a room too
                # (BE-20), a room with computers.
                computers = part.subject_id in computer_subjects
                if not (part.needs_room or part.id in needed or computers):
                    continue
                students = part_students(inp, part)
                cands = [
                    r.id
                    for r in rooms
                    if (part.room_type is None or r.type == part.room_type)
                    and (part.allowed_room_ids is None or r.id in part.allowed_room_ids)
                    and all(r.type == t for t in required.get(part.id, ()))
                    and (not computers or r.has_computers)
                    # Seats rule: only with a known seat count and a known head count.
                    and (students is None or r.seats is None or r.seats >= students)
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
        for w in _passes(tm):
            if not _in_week(tm, [part_course[pid]], w):
                continue
            for r, starts in by_room.items():
                for (d, s), lit in starts.items():
                    for p in range(s, s + size):
                        uses[r][w, d, p].append(lit)
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
    if tm.explain and c.hard:
        tm.model.add_assumption(lit)
    else:
        tm.model.add(lit == 1)


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


def _teacher_lits(tm: TimetableModel, teacher_id: int, banned, week=None) -> list:
    """Busy literals (one per taught period) in the slots where ``banned(day, period)``."""
    lits = []
    for course in tm.inp.courses:
        if _in_week(tm, [course.id], week) and any(
            teacher_id in p.teacher_ids for p in course.parts
        ):
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


def part_students(inp: SolverInput, part) -> int | None:
    """Students in a part: the sum over its groups (BE-20).

    A whole-class group counts the class's ``student_count``, any other group
    its own. If any of these is unknown the result is None and the seats rule
    is skipped for the part (ASSUMPTION: unknown means no limit, not zero).
    """
    classes = {k.id: k for k in inp.classes}
    groups = {g.id: g for g in inp.groups}
    total = 0
    for gid in part.group_ids:
        g = groups[gid]
        n = classes[g.class_id].student_count if g.is_whole else g.student_count
        if n is None:
            return None
        total += n
    return total


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
    # here. No candidate left -> INFEASIBLE. In explain mode (BE-11) the
    # candidates are not shrunk, so the literal bans the other room types instead.
    # Soft is a plain sum over room literals that already exist: one unit per
    # part-block placed in a room of another type. Parts that need no room are
    # not touched (ASSUMPTION: "prefer lab" never creates a room demand).
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
        if c.hard:
            if tm.explain:
                tm.model.add(cp_model.LinearExpr.sum(lits) == 0).only_enforce_if(
                    tm.enforce[c.id]
                )
        else:
            _limit(tm, c, cp_model.LinearExpr.sum(lits), 0, len(lits))


def _scope_combos(tm: TimetableModel, scope):
    """Yield ``(week, course ids)`` per student combination in scope and per week."""
    grade_of = {k.id: k.grade for k in tm.inp.classes}
    for class_id, cids in _student_combos(tm):
        if (
            scope == "all"
            or scope.get("class_id") == class_id
            or ("grade" in scope and grade_of[class_id] == scope["grade"])
        ):
            for w in _passes(tm):
                yield w, _in_week(tm, cids, w)


def _add_max_lessons_per_day(tm: TimetableModel, c) -> None:
    # ASSUMPTION: "lessons" are periods (a 2-period block counts 2), counted per
    # student combination, so a pupil of a split class is not charged for the
    # lessons of the other group. Soft pays per period over the maximum.
    cap = c.params["max"]
    for _week, cids in _scope_combos(tm, c.params["scope"]):
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


def _add_max_same_subject_per_day(tm: TimetableModel, c) -> None:
    # ASSUMPTION: like max_lessons_per_day, counted in periods per student
    # combination, but only for courses with a part of this subject.
    # Soft pays per period over the maximum.
    cap, subject = c.params["max"], c.params["subject_id"]
    of_subject = {
        k.id for k in tm.inp.courses if any(p.subject_id == subject for p in k.parts)
    }
    for _week, cids in _scope_combos(tm, c.params["scope"]):
        for d in tm.inp.days:
            lits = [
                x
                for cid in cids
                if cid in of_subject
                for p in tm.inp.periods
                for x in tm.busy[cid].get((d, p), ())
            ]
            if len(lits) > cap:
                expr = cp_model.LinearExpr.sum(lits)
                _limit(tm, c, expr, cap, len(tm.inp.periods))


def _add_teacher_max_lessons_per_day(tm: TimetableModel, c) -> None:
    # ASSUMPTION: "lessons" are periods taught (a 2-period block counts 2).
    # Soft pays per period over the maximum.
    cap = c.params["max"]
    for w in _passes(tm):
        for d in tm.inp.days:
            lits = _teacher_lits(
                tm, c.params["teacher_id"], lambda day, p, d=d: day == d, w
            )
            if len(lits) > cap:
                _limit(tm, c, cp_model.LinearExpr.sum(lits), cap, len(tm.inp.periods))


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
    for week, cids in _scope_combos(tm, c.params["scope"]):
        for d in tm.inp.days:
            occ = {
                p: [x for cid in cids for x in tm.busy[cid].get((d, p), ())]
                for p in periods
            }
            before = _reach(m, occ, periods, f"before{c.id}{week}d{d}")
            after = _reach(m, occ, periods[::-1], f"after{c.id}{week}d{d}")
            for p in periods[1:-1]:
                if before[p] is not None and after[p] is not None:
                    expr = before[p] + after[p] - cp_model.LinearExpr.sum(occ[p])
                    _limit(tm, c, expr, 1, 2)


def _add_day_starts_first_period(tm: TimetableModel, c) -> None:
    # ASSUMPTION: like no_gaps, per student combination: on a day the
    # combination has any lesson, it has one in the first period. A day without
    # lessons is fine. Soft pays ``weight`` per combination and day that starts late.
    m, first = tm.model, tm.inp.periods[0]
    for week, cids in _scope_combos(tm, c.params["scope"]):
        for d in tm.inp.days:
            occ = {
                p: [x for cid in cids for x in tm.busy[cid].get((d, p), ())]
                for p in tm.inp.periods
            }
            later = [p for p in tm.inp.periods[1:] if occ[p]]
            if not later:
                continue
            # The group rule keeps each sum at 0 or 1 for one combination.
            at_first = cp_model.LinearExpr.sum(occ[first])
            if c.hard:
                for p in later:
                    m.add(cp_model.LinearExpr.sum(occ[p]) <= at_first).only_enforce_if(
                        tm.enforce[c.id]
                    )
            else:
                late = m.new_bool_var(f"late{c.id}{week}d{d}")
                for p in later:
                    m.add(late >= cp_model.LinearExpr.sum(occ[p]) - at_first)
                tm.penalties.append((c.weight, late))


def _add_periods_reserved_for_subjects(tm: TimetableModel, c) -> None:
    # ASSUMPTION: a block runs all parts of its course at once, so a course may
    # use a reserved period only when every part has one of the listed subjects.
    # Soft pays per period of another course in a reserved period.
    reserved, allowed = set(c.params["periods"]), set(c.params["subject_ids"])
    lits = [
        x
        for course in tm.inp.courses
        if any(part.subject_id not in allowed for part in course.parts)
        for (_, p), ls in tm.busy[course.id].items()
        if p in reserved
        for x in ls
    ]
    if lits:
        _limit(tm, c, cp_model.LinearExpr.sum(lits), 0, len(lits))


def _banned_slot_lits(tm: TimetableModel, course_ids, slots) -> list:
    return [
        x
        for cid in course_ids
        for (d, p), ls in tm.busy[cid].items()
        if (d, p) in slots
        for x in ls
    ]


def _add_class_unavailable_slots(tm: TimetableModel, c) -> None:
    # ASSUMPTION: every course with a part for any group of the class is banned,
    # whole-class or split group. Soft pays per period inside the listed slots.
    in_class = {g.id for g in tm.inp.groups if g.class_id == c.params["class_id"]}
    cids = [
        k.id
        for k in tm.inp.courses
        if any(set(p.group_ids) & in_class for p in k.parts)
    ]
    slots = {(s["day"], s["period"]) for s in c.params["slots"]}
    lits = _banned_slot_lits(tm, cids, slots)
    if lits:
        _limit(tm, c, cp_model.LinearExpr.sum(lits), 0, len(lits))


def _add_subject_unavailable_slots(tm: TimetableModel, c) -> None:
    # Like max_same_subject_per_day: courses with a part of this subject.
    # Soft pays per period inside the listed slots.
    cids = [
        k.id
        for k in tm.inp.courses
        if any(p.subject_id == c.params["subject_id"] for p in k.parts)
    ]
    slots = {(s["day"], s["period"]) for s in c.params["slots"]}
    lits = _banned_slot_lits(tm, cids, slots)
    if lits:
        _limit(tm, c, cp_model.LinearExpr.sum(lits), 0, len(lits))


def _teacher_day_occ(tm: TimetableModel, c, week, day) -> dict:
    return {
        p: _teacher_lits(
            tm,
            c.params["teacher_id"],
            lambda d, q, day=day, p=p: (d, q) == (day, p),
            week,
        )
        for p in tm.inp.periods
    }


def _add_teacher_max_gaps(tm: TimetableModel, c) -> None:
    # ASSUMPTION: a gap is a free period between the teacher's first and last
    # lesson of one day (like no_gaps, for a teacher); counted per day, not per
    # week. Soft pays ``weight`` per gap above the maximum.
    m, periods, cap = tm.model, tm.inp.periods, c.params["max"]
    for week in _passes(tm):
        for d in tm.inp.days:
            occ = _teacher_day_occ(tm, c, week, d)
            before = _reach(m, occ, periods, f"tbefore{c.id}{week}d{d}")
            after = _reach(m, occ, periods[::-1], f"tafter{c.id}{week}d{d}")
            gaps = []
            for p in periods[1:-1]:
                if before[p] is not None and after[p] is not None:
                    gap = m.new_bool_var(f"tgap{c.id}{week}d{d}p{p}")
                    m.add(
                        gap
                        >= before[p] + after[p] - cp_model.LinearExpr.sum(occ[p]) - 1
                    )
                    gaps.append(gap)
            if len(gaps) > cap:
                _limit(tm, c, cp_model.LinearExpr.sum(gaps), cap, len(gaps))


def _add_teacher_min_lessons_per_day(tm: TimetableModel, c) -> None:
    # ASSUMPTION: a day without lessons is fine (the minimum applies only to
    # days the teacher teaches); periods are counted, a 2-period block is 2.
    # Soft pays per period short of the minimum.
    m, floor = tm.model, c.params["min"]
    for week in _passes(tm):
        for d in tm.inp.days:
            lits = [x for ls in _teacher_day_occ(tm, c, week, d).values() for x in ls]
            if not lits:
                continue
            used = m.new_bool_var(f"tused{c.id}{week}d{d}")
            m.add_max_equality(used, lits)
            n = cp_model.LinearExpr.sum(lits)
            if c.hard:
                m.add(n >= floor * used).only_enforce_if(tm.enforce[c.id])
            else:
                short = m.new_int_var(0, floor, f"tshort{c.id}{week}d{d}")
                m.add(short >= floor * used - n)
                tm.penalties.append((c.weight, short))


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
    "max_same_subject_per_day": _add_max_same_subject_per_day,
    "teacher_max_lessons_per_day": _add_teacher_max_lessons_per_day,
    "day_starts_first_period": _add_day_starts_first_period,
    "periods_reserved_for_subjects": _add_periods_reserved_for_subjects,
    "class_unavailable_slots": _add_class_unavailable_slots,
    "subject_unavailable_slots": _add_subject_unavailable_slots,
    "teacher_max_gaps": _add_teacher_max_gaps,
    "teacher_min_lessons_per_day": _add_teacher_min_lessons_per_day,
}


# Cost of moving one block (day + start period) away from the reference. Soft
# constraint weights are positive integers, so one moved block costs as much as a
# weight-1 violation. ASSUMPTION: a room change alone is not a move.
MOVE_WEIGHT = 1


def _add_reference(tm: TimetableModel) -> None:
    ref = defaultdict(set)  # course id -> {(day, start period, length)}
    for x in tm.reference:
        ref[x.course_id].add((x.day, x.start_period, x.length))
    for course in tm.inp.courses:
        for size in set(course.block_sizes):
            blocks = [b for b, n in enumerate(course.block_sizes) if n == size]
            # Blocks of one size are interchangeable: any of them may keep a slot.
            slots = sorted(k for k in ref[course.id] if k[2] == size)[: len(blocks)]
            for i, (d, s, _) in enumerate(slots):
                lits = [tm.start[course.id, b].get((d, s)) for b in blocks]
                lits = [x for x in lits if x is not None]
                if lits:
                    tm.model.add_hint(lits[min(i, len(lits) - 1)], 1)
                    # At most one literal is true: equal blocks cannot overlap.
                    tm.penalties.append(
                        (MOVE_WEIGHT, 1 - cp_model.LinearExpr.sum(lits))
                    )


def count_moved(reference, lessons) -> int:
    """Blocks of courses present in both that changed day or start period."""
    old, new = defaultdict(set), defaultdict(set)
    for x in reference:
        old[x.course_id].add((x.day, x.start_period, x.length))
    for x in lessons:
        new[x.course_id].add((x.day, x.start_period, x.length))
    return sum(len(v - old[c]) for c, v in new.items() if c in old)


def add_constraints(tm: TimetableModel) -> None:
    """Add every user constraint, then one objective from the soft ones."""
    for c in tm.inp.constraints:
        _enforce(tm, c)
        _ADDERS[c.type](tm, c)
    _add_reference(tm)
    if tm.penalties:
        tm.model.minimize(sum(w * e for w, e in tm.penalties))


def _at_most(tm: TimetableModel, course_ids: list[int], cap: int) -> None:
    for w in _passes(tm):
        ids = _in_week(tm, course_ids, w)
        if len(ids) <= cap:
            continue
        slots = {k for cid in ids for k in tm.busy[cid]}
        for k in slots:
            lits = [x for cid in ids for x in tm.busy[cid].get(k, ())]
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
    soft = any(not c.hard for c in tm.inp.constraints) or bool(tm.reference)
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
    reference=(),
) -> SolveResult:
    """Build a timetable that satisfies the built-in rules and the constraints.

    ``reference`` (previous ``LessonPlacement`` rows) makes moving a block away
    from its previous slot cost ``MOVE_WEIGHT`` (BE-13).
    """
    found = inp.problems()
    if found:
        raise ContractError(found)
    tm = _build(inp, reference=tuple(reference))
    result = run(tm, time_limit_s, num_workers, random_seed)
    if result.status == "INFEASIBLE" and any(c.hard for c in inp.constraints):
        ids = _conflicting_ids(inp, time_limit_s, random_seed)
        result = replace(result, conflicting_constraint_ids=ids)
    return result


def _build(inp: SolverInput, explain: bool = False, reference=()) -> TimetableModel:
    tm = new_model(inp, explain)
    tm.reference = reference
    for rule in BUILTIN_RULES:
        rule(tm)
    add_constraints(tm)
    return tm


# shortcut: the core is sufficient, not minimal, and the explain solve gets a short
# cap; an empty result means "conflict in the base data, or unclear" (upgrade: deletion
# filter to shrink the core).
EXPLAIN_TIME_LIMIT_S = 20


def _conflicting_ids(
    inp: SolverInput, time_limit_s: float, random_seed: int
) -> tuple[int, ...]:
    """Ids of hard constraints that together make the problem infeasible (BE-11).

    Empty when the base rules alone are infeasible or the solve timed out.
    """
    tm = _build(inp, explain=True)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = min(time_limit_s, EXPLAIN_TIME_LIMIT_S)
    solver.parameters.num_workers = (
        1  # core extraction is only reliable single-threaded
    )
    solver.parameters.random_seed = random_seed
    if solver.solve(tm.model) != cp_model.INFEASIBLE:
        return ()
    by_index = {lit.index: cid for cid, lit in tm.enforce.items()}
    core = solver.sufficient_assumptions_for_infeasibility()
    return tuple(sorted(by_index[i] for i in core if i in by_index))
