"""Test-only re-check of the built-in rules and user constraints on a SolveResult.

Deliberately separate from checker/ (owned by the other developer, CLAUDE.md):
this only lets solver tests assert that a result is valid.
"""

from collections import Counter, defaultdict
from itertools import pairwise, product


def violations(inp, result) -> list[str]:
    out = []
    parts = {p.id: (c, p) for c in inp.courses for p in c.parts}
    promoted = {  # parts a hard room_type_required makes need a room
        pid
        for c in inp.constraints
        if c.hard and c.type == "room_type_required"
        for pid in _matching(inp, c.params)
    }
    groups = {g.id: g for g in inp.groups}
    rooms = {r.id: r for r in inp.rooms}
    last = inp.periods[-1]

    by_part = defaultdict(list)
    for les in result.lessons:
        by_part[les.part_id].append(les)
        if les.day not in inp.days or les.start_period not in inp.periods:
            out.append(f"{les}: outside the week")
        if les.start_period + les.length - 1 > last:
            out.append(f"{les}: ends after the day")
        if parts[les.part_id][0].id != les.course_id:
            out.append(f"{les}: part not in course")

    for c in inp.courses:
        blocks = None
        for p in c.parts:
            mine = sorted((x.day, x.start_period, x.length) for x in by_part[p.id])
            if sorted(x[2] for x in mine) != sorted(c.block_sizes):
                out.append(f"course {c.id} part {p.id}: block lengths {mine}")
            for (d1, s1, n1), (d2, s2, _) in pairwise(mine):
                if d1 == d2 and s2 <= s1 + n1:  # sorted, so only neighbours matter
                    out.append(f"course {c.id} part {p.id}: blocks touch on day {d1}")
            if blocks is not None and mine != blocks:
                out.append(f"course {c.id}: parts not at the same time")
            blocks = mine
            for x in by_part[p.id]:
                if (p.needs_room or p.id in promoted) != (x.room_id is not None):
                    out.append(f"{x}: room given iff needs_room")
                elif x.room_id is not None:
                    r = rooms[x.room_id]
                    if (p.room_type and r.type != p.room_type) or (
                        p.allowed_room_ids is not None
                        and r.id not in p.allowed_room_ids
                    ):
                        out.append(f"{x}: room does not match")

    teacher_use, group_use, room_use = Counter(), Counter(), Counter()
    for les in result.lessons:
        _, p = parts[les.part_id]
        for period in range(les.start_period, les.start_period + les.length):
            slot = (les.day, period)
            for t in p.teacher_ids:
                teacher_use[t, slot] += 1
            for g in p.group_ids:
                group_use[g, slot] += 1
            if les.room_id is not None:
                room_use[les.room_id, slot] += 1
    out += [f"teacher {t} twice at {s}" for (t, s), n in teacher_use.items() if n > 1]
    out += [
        f"room {r} over capacity at {s}"
        for (r, s), n in room_use.items()
        if n > rooms[r].concurrent_capacity
    ]
    # Two groups clash if same class and (either is whole, same group, or
    # different splits). Re-implemented here on purpose (not imported).
    busy = defaultdict(list)  # (class, slot) -> group ids, one entry per lesson
    for (g, s), n in group_use.items():
        busy[groups[g].class_id, s] += [g] * n
    for (_, s), gids in busy.items():
        for i, a in enumerate(gids):
            for b in gids[i + 1 :]:
                ga, gb = groups[a], groups[b]
                if ga.is_whole or gb.is_whole or a == b or ga.split != gb.split:
                    out.append(f"groups {a} and {b} clash at {s}")
    return out
    for con in inp.constraints:
        if con.hard and (n := measure(inp, result, con)):
            out.append(f"constraint {con.id} ({con.type}) broken {n} time(s)")
    return out


def soft_penalty(inp, result) -> int:
    """Sum of weight x violations over the soft constraints (the expected objective)."""
    return sum(
        con.weight * measure(inp, result, con)
        for con in inp.constraints
        if not con.hard
    )


def _matching(inp, params):
    return {
        p.id
        for c in inp.courses
        for p in c.parts
        if c.id == params.get("course_id") or p.subject_id == params.get("subject_id")
    }


def _combos(inp, scope):
    """(class id, set of group ids of one student combination) in the scope."""
    grade = {c.id: c.grade for c in inp.classes}
    for c in inp.classes:
        if not (
            scope == "all"
            or scope.get("class_id") == c.id
            or ("grade" in scope and scope["grade"] == grade[c.id])
        ):
            continue
        mine = [g for g in inp.groups if g.class_id == c.id]
        splits = {}
        for g in mine:
            if not g.is_whole:
                splits.setdefault(g.split, []).append(g.id)
        whole = next(g.id for g in mine if g.is_whole)
        for pick in product(*splits.values()):
            yield c.id, {whole, *pick}


def measure(inp, result, con) -> int:
    """How much ``con`` is violated: 0 = holds. Soft penalty = weight x this."""
    p, hard = con.params, con.hard
    parts = {x.id: x for c in inp.courses for x in c.parts}
    rows = [(les, parts[les.part_id]) for les in result.lessons]
    periods = lambda les: range(les.start_period, les.start_period + les.length)
    if con.type in ("teacher_available_days", "teacher_unavailable_slots"):
        if con.type == "teacher_available_days":
            bad = lambda d, q: d not in p["days"]
        else:
            slots = {(s["day"], s["period"]) for s in p["slots"]}
            bad = lambda d, q: (d, q) in slots
        return sum(
            bad(les.day, q)
            for les, part in rows
            if p["teacher_id"] in part.teacher_ids
            for q in periods(les)
        )
    if con.type == "room_type_required":
        rooms = {r.id: r.type for r in inp.rooms}
        mine = _matching(inp, p)
        # soft: only part-blocks that have a room; hard: every one must have it
        return sum(
            (les.room_id is None and hard)
            or (les.room_id is not None and rooms[les.room_id] != p["room_type"])
            for les, part in rows
            if part.id in mine
        )
    if con.type == "fixed_slot":
        course = next(c for c in inp.courses if c.id == p["course_id"])
        mine = [les for les, part in rows if part.id == course.parts[0].id]
        # blocks[i] is block i; result rows come in block order (one per block)
        return sum(
            (les.day, les.start_period) != (b["day"], b["start_period"])
            for les, b in zip(mine, p["blocks"], strict=True)
        )
    # The rest are per student combination and day.
    total = 0
    for _, gids in _combos(inp, p["scope"]):
        for d in inp.days:
            seen = {
                q
                for les, part in rows
                if les.day == d and gids & set(part.group_ids)
                for q in periods(les)
            }
            if con.type == "max_lessons_per_day":
                total += max(0, len(seen) - p["max"])
            elif seen:  # no_gaps: free periods between first and last lesson
                total += max(seen) - min(seen) + 1 - len(seen)
    return total
