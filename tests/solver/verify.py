"""Test-only re-check of the built-in rules on a SolveResult.

Deliberately separate from checker/ (owned by the other developer, CLAUDE.md):
this only lets solver tests assert that a result is valid.
"""

from collections import Counter, defaultdict


def violations(inp, result) -> list[str]:
    out = []
    parts = {p.id: (c, p) for c in inp.courses for p in c.parts}
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
            if len({x[0] for x in mine}) != len(mine):
                out.append(f"course {c.id} part {p.id}: two blocks on one day")
            if blocks is not None and mine != blocks:
                out.append(f"course {c.id}: parts not at the same time")
            blocks = mine
            for x in by_part[p.id]:
                if p.needs_room != (x.room_id is not None):
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
    for (g, s), n in group_use.items():
        grp = groups[g]
        whole = next(
            x.id for x in groups.values() if x.class_id == grp.class_id and x.is_whole
        )
        extra = 0 if g == whole else group_use[whole, s]
        if n + extra > 1:
            out.append(f"group {g} conflicts at {s}")
    return out
