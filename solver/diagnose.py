"""Cheap, exact proofs that hard constraints cannot hold (FIX-UKTC).

CP-SAT needs tens of seconds, and often a lot more, to prove that a school's own rules
contradict each other, because the contradiction is plain counting that its search does
not see. This module does that counting first. Imports only ``schema.contract``.

Two kinds of overload are found, both only when a hard user constraint is what makes the
load not fit (an overload of the base data alone is left to the solver):

- teacher: the lessons the teacher gives in one week are more than the slots the teacher
  can use. Per day the usable slots are the periods not in a hard ``teacher_unavailable_slots``,
  on a day allowed by ``teacher_available_days``, capped by ``teacher_max_lessons_per_day``
  (a day with fewer usable slots than ``teacher_min_lessons_per_day`` can not be used at all,
  because the minimum applies to every day with a lesson).
- class: the lessons one kind of student (the whole class plus one group of each split, as in
  the solver) sits through in a week are more than the class's slots outside every hard
  ``class_unavailable_slots``.

Both tests are only necessary conditions (they ignore rooms, joint lessons, blocks that
need two free periods in a row), so a verdict "overloaded" is a proof, and "fine" is not.
Alternating weeks: every-week courses count in both weeks, a week-A course only in A, and
every week is tested on its own.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import product

from schema.contract import SolverInput

TEACHER_TYPES = (
    "teacher_unavailable_slots",
    "teacher_available_days",
    "teacher_max_lessons_per_day",
    "teacher_min_lessons_per_day",
)
CLASS_TYPES = ("class_unavailable_slots",)


@dataclass(frozen=True)
class Overload:
    kind: str  # "teacher" | "class"
    entity_id: int
    load: int  # periods per week to place (the worse week)
    capacity: int  # periods per week the hard constraints leave
    constraint_ids: tuple[int, ...]  # a sufficient set of the hard constraints to blame


def _weeks(inp: SolverInput) -> tuple:
    return ("A", "B") if any(c.week for c in inp.courses) else (None,)


def _load(inp: SolverInput, course_ids) -> int:
    """Periods per week in the worse week for these courses."""
    by_id = {c.id: c for c in inp.courses}
    worst = 0
    for week in _weeks(inp):
        worst = max(
            worst,
            sum(
                by_id[i].periods_per_week
                for i in course_ids
                if week is None or by_id[i].week in (None, week)
            ),
        )
    return worst


def _teacher_capacity(inp: SolverInput, teacher_id: int, cons) -> int:
    banned, cap, floor, days = set(), len(inp.periods), 0, set(inp.days)
    for c in cons:
        p = c.params
        if p.get("teacher_id") != teacher_id:
            continue
        if c.type == "teacher_unavailable_slots":
            banned |= {(s["day"], s["period"]) for s in p["slots"]}
        elif c.type == "teacher_available_days":
            days &= set(p["days"])
        elif c.type == "teacher_max_lessons_per_day":
            cap = min(cap, p["max"])
        elif c.type == "teacher_min_lessons_per_day":
            floor = max(floor, p["min"])
    total = 0
    for d in inp.days:
        if d not in days:
            continue
        free = sum(1 for p in inp.periods if (d, p) not in banned)
        usable = min(free, cap)
        total += usable if usable >= floor else 0
    return total


def _class_capacity(inp: SolverInput, class_id: int, cons) -> int:
    banned = set()
    for c in cons:
        if c.params.get("class_id") == class_id:
            banned |= {(s["day"], s["period"]) for s in c.params["slots"]}
    return len(inp.days) * len(inp.periods) - len(
        {(d, p) for d, p in banned if d in inp.days and p in inp.periods}
    )


def _shrink(overloaded, cons) -> tuple[int, ...]:
    """Drop every constraint that is not needed to keep the overload (deletion filter)."""
    keep = list(cons)
    for c in list(keep):
        rest = [x for x in keep if x is not c]
        if overloaded(rest):
            keep = rest
    return tuple(sorted(c.id for c in keep))


def find_overloads(inp: SolverInput) -> list[Overload]:
    """Teachers and classes whose weekly load cannot fit the hard constraints."""
    hard = [c for c in inp.constraints if c.hard]
    found = []
    courses_of = defaultdict(set)
    for course in inp.courses:
        for part in course.parts:
            for t in part.teacher_ids:
                courses_of[t].add(course.id)
    for teacher_id, ids in sorted(courses_of.items()):
        mine = [c for c in hard if c.type in TEACHER_TYPES]
        load = _load(inp, ids)
        base = len(inp.days) * len(inp.periods)

        def over(cons, t=teacher_id, load=load):
            return load > _teacher_capacity(inp, t, cons)

        if load <= base and over(mine):
            keep = _shrink(
                over, [c for c in mine if c.params.get("teacher_id") == teacher_id]
            )
            found.append(
                Overload(
                    "teacher",
                    teacher_id,
                    load,
                    _teacher_capacity(
                        inp, teacher_id, [c for c in mine if c.id in keep]
                    ),
                    keep,
                )
            )
    # Student combinations, exactly as the solver builds them.
    groups = {g.id: g for g in inp.groups}
    group_courses = defaultdict(set)
    for course in inp.courses:
        for part in course.parts:
            for gid in part.group_ids:
                group_courses[gid].add(course.id)
    whole = {g.class_id: g.id for g in groups.values() if g.is_whole}
    splits = defaultdict(lambda: defaultdict(list))
    for g in groups.values():
        if not g.is_whole:
            splits[g.class_id][g.split].append(g.id)
    mine_all = [c for c in hard if c.type in CLASS_TYPES]
    for class_id, w in sorted(whole.items()):
        worst = 0
        for combo in product(*splits[class_id].values()):
            ids = set(group_courses[w]).union(*(group_courses[g] for g in combo))
            worst = max(worst, _load(inp, ids))
        mine = [c for c in mine_all if c.params.get("class_id") == class_id]
        base = len(inp.days) * len(inp.periods)

        def over(cons, k=class_id, load=worst):
            return load > _class_capacity(inp, k, cons)

        if worst <= base and mine and over(mine):
            keep = _shrink(over, mine)
            found.append(
                Overload(
                    "class",
                    class_id,
                    worst,
                    _class_capacity(inp, class_id, [c for c in mine if c.id in keep]),
                    keep,
                )
            )
    return found
