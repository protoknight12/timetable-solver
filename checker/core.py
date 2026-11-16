"""Rules of the checker. Pure functions: plain data in, a list of ``Violation`` out.

Assumptions (none of these is a school regulation; each is also listed in the PR):

A1. Only OPTIMAL and FEASIBLE results are timetables. Any other status gives one
    hard ``no_timetable`` violation, so an empty list always means "checked, fine".
A2. ``SolverInput`` carries no constraint status, so every constraint in it is
    treated as confirmed (the web layer passes confirmed ones only).
A3. A row's index among the rows of one part is its block index (contract note,
    agreed 14.11.2026).
A4. All parts of a Course run at the same time (contract docstring), so block i
    of every part must have the same day and start period.
A5. ``no_gaps`` and ``max_lessons_per_day`` are counted per student combination:
    the whole-class group plus one group from each split of the class (groups of
    one split never share students, groups of different splits may). A period is
    busy for a combination when any of its groups has a lesson then. A class with
    no splits has one combination. Max: busy periods per day (a 2-period block
    counts 2). Gap: a free period with a busy one before and after it on the same
    day; free periods at the start or end of the day are not gaps. One violation
    per class and day, however many combinations break the rule.
A5b. ``max_same_subject_per_day`` is A5's max, counting only the periods of lessons
    whose part has that subject. ``teacher_max_lessons_per_day`` counts the distinct
    periods a teacher teaches per day (a 2-period block counts 2).
A5c. ``day_starts_first_period`` uses A5's combinations: on a day a combination
    has any busy period, the first period of the school day must be busy. One
    violation per class and day.
A5d. ``periods_reserved_for_subjects``: every row whose part has another subject
    and that covers a reserved period breaks it, once per such period.
A5e. Alternating weeks (``Course.week``: None = every week, "A", "B"). A course
    runs in week A if its week is None or "A", and in week B if None or "B".
    Two lessons can clash only if some week has both of them. So a teacher
    clash, a group clash and a room over capacity are judged per week: an A and
    a B course may share a slot, two A courses or an every-week and an A course
    may not. The per-day rules (A5, A5b, A5c and the teacher maximum) are
    evaluated per week and must hold in both weeks. One violation per entity
    and slot or day, whichever week breaks it. ``fixed_slot``, the teacher day
    and slot rules, ``room_type_required`` and ``periods_reserved_for_subjects``
    look at single rows and need no week split.
A5f. ``class_unavailable_slots`` bans every row whose part has a group of the class,
    ``subject_unavailable_slots`` every row whose part has the subject, once per
    covered banned slot (no week split). ``teacher_max_gaps``: free periods
    between a teacher's first and last taught period of one day, counted per day
    (not per week) and per week of A/B courses. ``teacher_min_lessons_per_day``:
    on a day (and week) with any lesson, at least ``min`` taught periods. One
    violation per teacher and day.
A6. ``room_type_required`` needs a room of that type on every affected row, even
    if the part says ``needs_room = false``.
A7. A room id on a part that does not need a room is allowed, and it still uses
    the room's capacity.
A8. Soft violations carry no weight; look it up through ``constraint_id``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations, product

from pydantic import ValidationError

from schema import constraints as cm
from schema.contract import LessonPlacement, SolveResult, SolverInput


@dataclass(frozen=True)
class Violation:
    """One broken rule.

    ``rule`` is the built-in rule or the constraint type. ``message_key`` names
    the exact problem in English; the web layer translates it to Bulgarian.
    ``entities`` are ``(kind, id)`` pairs such as ``("teacher", 3)``, ``("day", 1)``.
    ``constraint_id`` is set only for violations of a user constraint.
    """

    rule: str
    hard: bool
    message_key: str
    entities: tuple[tuple[str, int], ...] = ()
    constraint_id: int | None = None


def _slots(lesson: LessonPlacement) -> list[tuple[int, int]]:
    return [
        (lesson.day, p)
        for p in range(lesson.start_period, lesson.start_period + lesson.length)
    ]


def _runs_in(course, week: str) -> bool:
    return course.week in (None, week)


def _share_week(a, b) -> bool:
    """True if two courses run together in at least one of the weeks."""
    return a.week is None or b.week is None or a.week == b.week


def check(inp: SolverInput, result: SolveResult) -> list[Violation]:
    out: list[Violation] = []

    def add(rule, key, entities=(), hard=True, constraint_id=None) -> None:
        out.append(Violation(rule, hard, key, tuple(entities), constraint_id))

    # Input problems are reported as-is; unknown constraint types get their own
    # violation below.
    for p in inp.problems():
        if p.code != "unknown_constraint_type":
            add("input", p.code)

    if result.status not in ("OPTIMAL", "FEASIBLE"):
        add("status", "no_timetable")
        return out

    courses = {c.id: c for c in inp.courses}
    groups = {g.id: g for g in inp.groups}
    rooms = {r.id: r for r in inp.rooms}
    days, periods = set(inp.days), set(inp.periods)

    # --- lessons only reference existing things ---------------------------
    rows = []  # (lesson, course, part) for every lesson that makes sense
    for lesson in result.lessons:
        course = courses.get(lesson.course_id)
        if course is None:
            add("references", "unknown_course", [("course", lesson.course_id)])
            continue
        part = next((p for p in course.parts if p.id == lesson.part_id), None)
        if part is None:
            ents = [("course", course.id), ("part", lesson.part_id)]
            add("references", "unknown_part", ents)
            continue
        if lesson.room_id is not None and lesson.room_id not in rooms:
            # Still counted below, so it does not also look like a missing block.
            add("references", "unknown_room", [("room", lesson.room_id)])
        rows.append((lesson, course, part))

    by_part: dict[tuple[int, int], list[LessonPlacement]] = defaultdict(list)
    for lesson, course, part in rows:
        by_part[(course.id, part.id)].append(lesson)

    # --- every Course gets exactly its blocks ------------------------------
    for course in inp.courses:
        positions = {}
        for part in course.parts:
            lessons = by_part[(course.id, part.id)]
            ents = [("course", course.id), ("part", part.id)]
            if len(lessons) != len(course.block_sizes):
                add("blocks", "wrong_block_count", ents)
                continue
            for lesson, size in zip(lessons, course.block_sizes, strict=True):
                if lesson.length != size:
                    add("blocks", "wrong_block_length", ents)
                last = lesson.start_period + lesson.length - 1
                if (
                    lesson.day not in days
                    or lesson.start_period not in periods
                    or last not in periods
                ):
                    add("blocks", "block_outside_grid", ents + [("day", lesson.day)])
            for a, b in combinations(lessons, 2):
                if (
                    a.day == b.day
                    and a.start_period < b.start_period + b.length
                    and b.start_period < a.start_period + a.length
                ):
                    add("blocks", "blocks_overlap", ents + [("day", a.day)])
            positions[part.id] = [(x.day, x.start_period) for x in lessons]
        if len({tuple(v) for v in positions.values()}) > 1:
            add("blocks", "parts_not_simultaneous", [("course", course.id)])

    # --- teachers: one part-block per slot ---------------------------------
    teacher_at = defaultdict(list)
    for lesson, course, part in rows:
        for t in set(part.teacher_ids):
            for day, period in _slots(lesson):
                teacher_at[(t, day, period)].append(course)
    for (t, day, period), here in teacher_at.items():
        if any(_share_week(a, b) for a, b in combinations(here, 2)):
            ents = [("teacher", t), ("day", day), ("period", period)]
            add("teacher_conflict", "teacher_double_booked", ents)

    # --- student groups: conflicting groups never share a slot -------------
    def share_students(a_id: int, b_id: int) -> bool:
        a, b = groups.get(a_id), groups.get(b_id)
        if a is None or b is None or a.class_id != b.class_id:
            return False
        return a.is_whole or b.is_whole or a.id == b.id or a.split != b.split

    in_slot = defaultdict(list)
    for lesson, course, part in rows:
        for slot in _slots(lesson):
            in_slot[slot].append((course, part))
    for (day, period), items in in_slot.items():
        for (c1, p1), (c2, p2) in combinations(items, 2):
            if not _share_week(c1, c2):
                continue
            pair = next(
                (
                    (a, b)
                    for a in p1.group_ids
                    for b in p2.group_ids
                    if share_students(a, b)
                ),
                None,
            )
            if pair:
                ents = [("group", pair[0]), ("group", pair[1])]
                ents += [("day", day), ("period", period)]
                add("group_conflict", "groups_double_booked", ents)

    # --- rooms: capacity, and a matching room where one is needed ----------
    room_use: dict[tuple[int, str, int, int], int] = defaultdict(int)
    for lesson, course, part in rows:
        if lesson.room_id in rooms:
            for day, period in _slots(lesson):
                for w in ("A", "B"):
                    if _runs_in(course, w):
                        room_use[(lesson.room_id, w, day, period)] += 1
        if not part.needs_room or (
            lesson.room_id is not None and lesson.room_id not in rooms
        ):
            continue  # an unknown room is already reported
        ents = [("course", course.id), ("part", part.id)]
        room = rooms.get(lesson.room_id)
        if room is None:
            add("room_match", "room_missing", ents)
        elif (part.room_type is not None and room.type != part.room_type) or (
            part.allowed_room_ids is not None and room.id not in part.allowed_room_ids
        ):
            add("room_match", "room_mismatch", ents + [("room", room.id)])
    over = {
        (room_id, day, period)
        for (room_id, _w, day, period), n in room_use.items()
        if n > rooms[room_id].concurrent_capacity
    }
    for room_id, day, period in sorted(over):
        ents = [("room", room_id), ("day", day), ("period", period)]
        add("room_capacity", "room_over_capacity", ents)

    # --- user constraints --------------------------------------------------
    # (group_id, week, day) -> periods with a lesson
    group_busy = defaultdict(set)
    for lesson, course, part in rows:
        for w in ("A", "B"):
            if _runs_in(course, w):
                for g in part.group_ids:
                    for day, period in _slots(lesson):
                        group_busy[(g, w, day)].add(period)

    def class_days(class_id: int):
        """Yield (day, busy periods) for every student combination of the class."""
        mine = [g for g in inp.groups if g.class_id == class_id]
        splits = defaultdict(list)
        for g in mine:
            if not g.is_whole:
                splits[g.split].append(g.id)
        whole = [g.id for g in mine if g.is_whole]
        for pick in product(*splits.values()):
            for w, day in product(("A", "B"), inp.days):
                yield (
                    day,
                    set().union(*(group_busy[(g, w, day)] for g in whole + [*pick])),
                )

    def scoped_classes(scope) -> list[int]:
        if scope == "all":
            return [c.id for c in inp.classes]
        if isinstance(scope, cm.ClassScope):
            return [scope.class_id]
        return [c.id for c in inp.classes if c.grade == scope.grade]

    for con in inp.constraints:

        def viol(key, entities, con=con) -> None:
            add(con.type, key, entities, con.hard, con.id)

        if con.type not in {
            "teacher_available_days",
            "teacher_unavailable_slots",
            "room_type_required",
            "max_lessons_per_day",
            "no_gaps",
            "fixed_slot",
            "max_same_subject_per_day",
            "teacher_max_lessons_per_day",
            "day_starts_first_period",
            "periods_reserved_for_subjects",
            "class_unavailable_slots",
            "subject_unavailable_slots",
            "teacher_max_gaps",
            "teacher_min_lessons_per_day",
        }:
            add("unknown_constraint_type", "unknown_constraint_type", (), True, con.id)
            continue
        raw = {"type": con.type, "hard": con.hard, "params": con.params}
        if con.weight is not None:
            raw["weight"] = con.weight
        try:
            params = cm.parse_constraint(raw).params  # type: ignore[attr-defined]
        except ValidationError:
            add(con.type, "invalid_constraint_params", (), True, con.id)
            continue

        if con.type == "teacher_available_days":
            for lesson, course, part in rows:
                if params.teacher_id in part.teacher_ids and (
                    lesson.day not in params.days
                ):
                    viol(
                        "teacher_unavailable_day",
                        [("teacher", params.teacher_id), ("course", course.id)]
                        + [("day", lesson.day)],
                    )
        elif con.type == "teacher_unavailable_slots":
            blocked = {(s.day, s.period) for s in params.slots}
            for lesson, course, part in rows:
                if params.teacher_id in part.teacher_ids:
                    for day, period in _slots(lesson):
                        if (day, period) in blocked:
                            viol(
                                "teacher_unavailable_slot",
                                [("teacher", params.teacher_id), ("course", course.id)]
                                + [("day", day), ("period", period)],
                            )
        elif con.type in ("class_unavailable_slots", "subject_unavailable_slots"):
            blocked = {(s.day, s.period) for s in params.slots}
            by_class = con.type == "class_unavailable_slots"
            members = (
                {g.id for g in inp.groups if g.class_id == params.class_id}
                if by_class
                else set()
            )
            for lesson, course, part in rows:
                if by_class and not members & set(part.group_ids):
                    continue
                if not by_class and part.subject_id != params.subject_id:
                    continue
                who = ("class", params.class_id) if by_class else None
                who = who or ("subject", params.subject_id)
                key = "class" if by_class else "subject"
                for day, period in _slots(lesson):
                    if (day, period) in blocked:
                        ents = [who, ("course", course.id), ("day", day)]
                        viol(f"{key}_unavailable_slot", ents + [("period", period)])
        elif con.type in ("teacher_max_gaps", "teacher_min_lessons_per_day"):
            taught = defaultdict(set)  # (week, day) -> periods the teacher teaches
            for lesson, course, part in rows:
                if params.teacher_id in part.teacher_ids:
                    for w in ("A", "B"):
                        if _runs_in(course, w):
                            for day, period in _slots(lesson):
                                taught[(w, day)].add(period)
            for day in sorted({d for _, d in taught}):
                for busy in (taught[(w, day)] for w in ("A", "B")):
                    if not busy:
                        continue
                    if con.type == "teacher_max_gaps":
                        bad = max(busy) - min(busy) + 1 - len(busy) > params.max
                    else:
                        bad = len(busy) < params.min
                    if bad:
                        viol(
                            "teacher_too_many_gaps"
                            if con.type == "teacher_max_gaps"
                            else "teacher_too_few_lessons",
                            [("teacher", params.teacher_id), ("day", day)],
                        )
                        break
        elif con.type == "room_type_required":
            for lesson, course, part in rows:
                if (
                    params.course_id == course.id
                    or params.subject_id == part.subject_id
                ):
                    room = rooms.get(lesson.room_id)
                    if room is None or room.type != params.room_type:
                        viol(
                            "room_type_not_met",
                            [("course", course.id), ("part", part.id)],
                        )
        elif con.type == "max_lessons_per_day":
            for class_id in scoped_classes(params.scope):
                bad = {d for d, busy in class_days(class_id) if len(busy) > params.max}
                for day in sorted(bad):
                    viol("too_many_lessons", [("class", class_id), ("day", day)])
        elif con.type == "max_same_subject_per_day":
            # Periods of this subject per student combination and day (A5).
            subj_busy = defaultdict(set)  # (group_id, week, day) -> periods
            for lesson, course, part in rows:
                if part.subject_id == params.subject_id:
                    for w in ("A", "B"):
                        if _runs_in(course, w):
                            for g in part.group_ids:
                                for day, period in _slots(lesson):
                                    subj_busy[(g, w, day)].add(period)
            for class_id in scoped_classes(params.scope):
                mine = [g for g in inp.groups if g.class_id == class_id]
                splits = defaultdict(list)
                for g in mine:
                    if not g.is_whole:
                        splits[g.split].append(g.id)
                whole = [g.id for g in mine if g.is_whole]
                bad = {
                    day
                    for pick in product(*splits.values())
                    for w, day in product(("A", "B"), inp.days)
                    if len(
                        set().union(*(subj_busy[(g, w, day)] for g in whole + [*pick]))
                    )
                    > params.max
                }
                for day in sorted(bad):
                    viol(
                        "too_many_same_subject",
                        [("class", class_id), ("subject", params.subject_id)]
                        + [("day", day)],
                    )
        elif con.type == "teacher_max_lessons_per_day":
            taught = defaultdict(set)  # (week, day) -> periods the teacher teaches
            for lesson, course, part in rows:
                if params.teacher_id in part.teacher_ids:
                    for w in ("A", "B"):
                        if _runs_in(course, w):
                            for day, period in _slots(lesson):
                                taught[(w, day)].add(period)
            for day in sorted({d for _, d in taught}):
                if any(len(taught[(w, day)]) > params.max for w in ("A", "B")):
                    viol(
                        "teacher_too_many_lessons",
                        [("teacher", params.teacher_id), ("day", day)],
                    )
        elif con.type == "no_gaps":
            for class_id in scoped_classes(params.scope):
                bad = {
                    d
                    for d, busy in class_days(class_id)
                    if busy and len(busy) < max(busy) - min(busy) + 1
                }
                for day in sorted(bad):
                    viol("gap_in_day", [("class", class_id), ("day", day)])
        elif con.type == "day_starts_first_period":
            first = min(inp.periods)
            for class_id in scoped_classes(params.scope):
                bad = {
                    d for d, busy in class_days(class_id) if busy and first not in busy
                }
                for day in sorted(bad):
                    viol("day_starts_late", [("class", class_id), ("day", day)])
        elif con.type == "periods_reserved_for_subjects":
            reserved, allowed = set(params.periods), set(params.subject_ids)
            for lesson, course, part in rows:
                if part.subject_id in allowed:
                    continue
                for day, period in _slots(lesson):
                    if period in reserved:
                        viol(
                            "period_reserved_for_other_subjects",
                            [("course", course.id), ("part", part.id)]
                            + [("day", day), ("period", period)],
                        )
        elif con.type == "fixed_slot":
            course = courses.get(params.course_id)
            for part in course.parts if course else ():
                lessons = by_part[(course.id, part.id)]
                if len(lessons) != len(params.blocks):
                    continue  # already reported as wrong_block_count
                for i, (lesson, b) in enumerate(
                    zip(lessons, params.blocks, strict=True)
                ):
                    if (lesson.day, lesson.start_period) != (b.day, b.start_period):
                        viol(
                            "block_not_at_fixed_slot",
                            [("course", course.id), ("part", part.id), ("block", i)],
                        )
    return out
