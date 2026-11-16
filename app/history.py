"""Versions of the timetable (BE-21): how a version was made and what changed."""

from __future__ import annotations

from collections import Counter, defaultdict

from schema.contract import LessonPlacement


def how_made(job) -> tuple[str, str]:
    """(kind, Bulgarian label), derived from existing columns.

    A job that never started was copied from ``base_job_id``: a restore today,
    a manual edit from FE-15 later. FE-15 must tell the two apart here (it may
    need its own marker; adding a column needs Boyan's approval first).
    """
    if job.started_at is None:
        base = f" №{job.base_job_id}" if job.base_job_id else ""
        return "copy", f"Копие на версия{base} (възстановена или ръчно редактирана)"
    if job.base_job_id:
        return "resolved", f"Преизчислена с минимални промени спрямо №{job.base_job_id}"
    return "solved", "Изчислена наново"


def diff_lessons(old: list[LessonPlacement], new: list[LessonPlacement]) -> list[dict]:
    """Changes between two versions as dicts ``{kind, before, after}``.

    kind: added, removed, moved (day or start period changed) or room (only the
    room changed). Blocks are matched inside the same part and length;
    identical blocks cancel out.
    """
    by_part = defaultdict(lambda: ([], []))
    for x in old:
        by_part[x.part_id][0].append(x)
    for x in new:
        by_part[x.part_id][1].append(x)
    out = []
    for before, after in by_part.values():
        same = Counter(before) & Counter(after)
        before = list((Counter(before) - same).elements())
        after = list((Counter(after) - same).elements())
        # Sorted so the pairing (and so the page and the tests) is deterministic.
        before.sort(key=lambda x: (x.day, x.start_period, x.room_id or 0))
        after.sort(key=lambda x: (x.day, x.start_period, x.room_id or 0))
        for kind, match in (
            ("room", lambda a, b: (a.day, a.start_period) == (b.day, b.start_period)),
            ("moved", lambda a, b: True),
        ):
            for b in before[:]:
                a = next(
                    (a for a in after if a.length == b.length and match(a, b)), None
                )
                if a:
                    before.remove(b)
                    after.remove(a)
                    out.append({"kind": kind, "before": b, "after": a})
        out += [{"kind": "removed", "before": b, "after": None} for b in before]
        out += [{"kind": "added", "before": None, "after": a} for a in after]
    return out
