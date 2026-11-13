"""Convert seeds/example_school.json into a contract ``SolverInput``.

Pure function, no DB, so BE-04 can reuse it for seeding.
Run: ``uv run python -m tools.seed_to_contract`` to print a summary and problems.

Mapping:
- text ids -> ints 1..n in file order; days 0..4 -> 1..5; periods from meta.periods.
- every class gets a whole group; each distinct (class, label) becomes a split
  group; ``group: null`` means the whole class.
- splits (TECH_STACK §5, group splits): labels with "група" ("Първа група", "Втора група") form the
  split "групи"; every other label is a language ("Немски", "Испански",
  "Китайски") and forms the split "езици". ASSUMPTION: a student is in at most
  one language group, and language groups cut across Група 1 / Група 2. This
  matches the reference timetable, where the two never run at the same time.
- each curriculum item -> one Course with one part. The file merges parallel
  lessons into separate items, so parallel parts cannot be recovered from it.
- a room is required only when ALL reference placements of the item use rooms
  of one non-regular type (lab, gym); otherwise the item gets no room.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from schema.contract import (
    Course,
    CoursePart,
    Room,
    SchoolClass,
    SolverInput,
    StudentGroup,
    Subject,
    Teacher,
)

SEED = Path(__file__).resolve().parents[1] / "seeds" / "example_school.json"
# meta.assumptions: the sports hall "СБ" holds several PE lessons at once.
GYM_CAPACITY = 3


def split_of(label: str) -> str:
    """Split name for a seed group label (see the module docstring)."""
    return "групи" if "група" in label.lower() else "езици"


def _key(item: dict) -> tuple:
    """Identity of a curriculum item, as build_seed.py groups activities."""
    groups = tuple(sorted((c["class"], c["group"] or "") for c in item["classes"]))
    return groups, item["subject"], item["teacher"]


def seed_to_solver_input(seed: dict) -> SolverInput:
    def ids(items):
        return {x["id"]: i for i, x in enumerate(items, 1)}

    teacher, subject = ids(seed["teachers"]), ids(seed["subjects"])
    klass, room = ids(seed["classes"]), ids(seed["rooms"])
    room_type = {r["id"]: r["type"] for r in seed["rooms"]}

    groups = [
        StudentGroup(klass[c["id"]], klass[c["id"]], True, "цял клас")
        for c in seed["classes"]
    ]
    group_id = {(c["id"], None): klass[c["id"]] for c in seed["classes"]}
    for item in seed["curriculum"]:
        for c in item["classes"]:
            k = (c["class"], c["group"])
            if k not in group_id:
                group_id[k] = len(group_id) + 1
                groups.append(
                    StudentGroup(group_id[k], klass[k[0]], False, k[1], split_of(k[1]))
                )

    placed_types = {}
    for a in seed["reference_timetable"]:
        placed_types.setdefault(_key(a), set()).add(room_type.get(a["room"], "regular"))

    courses = []
    for i, item in enumerate(seed["curriculum"], 1):
        types = placed_types.get(_key(item), set())
        rtype = next(iter(types)) if len(types) == 1 else None
        needs = rtype not in (None, "regular")
        part = CoursePart(
            id=i,
            subject_id=subject[item["subject"]],
            group_ids=tuple(group_id[c["class"], c["group"]] for c in item["classes"]),
            teacher_ids=(teacher[item["teacher"]],),
            needs_room=needs,
            room_type=rtype if needs else None,
        )
        courses.append(
            Course(i, item["periods_per_week"], tuple(item["blocks"]), (part,))
        )

    return SolverInput(
        days=tuple(range(1, len(seed["meta"]["days"]) + 1)),
        periods=tuple(p["number"] for p in seed["meta"]["periods"]),
        teachers=tuple(Teacher(i) for i in teacher.values()),
        subjects=tuple(Subject(i) for i in subject.values()),
        classes=tuple(
            SchoolClass(klass[c["id"]], c["name"], c.get("grade"))
            for c in seed["classes"]
        ),
        groups=tuple(groups),
        rooms=tuple(
            Room(
                room[r["id"]],
                r["type"],
                GYM_CAPACITY if r["type"] == "gym" else 1,
            )
            for r in seed["rooms"]
        ),
        courses=tuple(courses),
    )


def load_seed(path: Path = SEED) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    inp = seed_to_solver_input(load_seed())
    print(
        f"{len(inp.teachers)} teachers, {len(inp.classes)} classes, "
        f"{len(inp.groups)} groups, {len(inp.rooms)} rooms, {len(inp.courses)} courses, "
        f"{sum(c.periods_per_week for c in inp.courses)} periods/week, "
        f"{sum(p.needs_room for c in inp.courses for p in c.parts)} parts need a room"
    )
    for p in inp.problems():
        print(p)
