"""Second anonymization pass for the example school seed.

``build_seed.py`` already replaces teacher and class names. This pass also
hides details that could identify the real school (decided 14.11.2026,
TECH_STACK §15 q12, option "anonymize further"):

- rooms get generic names and ids ("Стая 1", "Лаборатория 1", "Физкултурен
  салон 1"), numbered in file order within each type; real room numbers are gone;
- bell times are replaced by a generic schedule (7:30 start, 45-minute lessons);
- the days of the reference timetable are permuted, so it is not the school's
  real week, and its lessons are re-sorted and renumbered.

Kept as-is: room types and counts, subjects, the curriculum and the structure
of the reference timetable (joint lessons, splits, block lengths). The solver
never reads bell times or day names, so nothing it depends on changes.

The pass is idempotent: an already anonymized seed (``meta.anonymized``) is
returned unchanged.

Usage: python tools/asc_import/anonymize.py seeds/example_school.json
"""

import json
import sys
from pathlib import Path

# Old day index -> new day index. Fixed, so the output is reproducible.
DAY_PERMUTATION = (2, 4, 0, 3, 1)

ROOM_NAMES = {
    "regular": ("R", "Стая {}"),
    "lab": ("L", "Лаборатория {}"),
    "gym": ("G", "Физкултурен салон {}"),
}

FIRST_START_MIN = 7 * 60 + 30
LESSON_MIN = 45
BREAK_MIN = 10
LONG_BREAK_MIN = 20
LONG_BREAK_AFTER = 3


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60}:{minutes % 60:02d}"


def generic_bell_times(numbers: list[int]) -> list[dict]:
    out, t = [], FIRST_START_MIN
    for n in numbers:
        out.append({"number": n, "start": _hhmm(t), "end": _hhmm(t + LESSON_MIN)})
        t += LESSON_MIN + (LONG_BREAK_MIN if n == LONG_BREAK_AFTER else BREAK_MIN)
    return out


def anonymize(seed: dict) -> dict:
    """Return an anonymized copy of ``seed`` (unchanged if already done)."""
    seed = json.loads(json.dumps(seed))  # deep copy
    meta = seed["meta"]
    if meta.get("anonymized"):
        return seed

    counters: dict[str, int] = {}
    room_map: dict[str, str] = {}
    for room in seed["rooms"]:
        prefix, label = ROOM_NAMES.get(room["type"], ("X", "Помещение {}"))
        counters[room["type"]] = k = counters.get(room["type"], 0) + 1
        room_map[room["id"]] = f"{prefix}{k:02d}"
        room["id"], room["name"] = room_map[room["id"]], label.format(k)

    acts = seed["reference_timetable"]
    for a in acts:
        a["day"] = DAY_PERMUTATION[a["day"]]
        if a.get("room") is not None:
            a["room"] = room_map[a["room"]]
    acts.sort(key=lambda a: (a["day"], a["start_period"], a["teacher"], a["subject"]))
    for i, a in enumerate(acts, 1):
        a["id"] = f"A{i:03d}"

    meta["periods"] = generic_bell_times([p["number"] for p in meta["periods"]])
    meta["description"] = (
        "Anonymized from a real aSc timetable export. Teacher and class names are "
        "generated; rooms are renamed, bell times are generic and the days of the "
        "reference timetable are permuted. Subjects, curriculum and the timetable "
        "structure are kept."
    )
    meta["assumptions"] = [
        "Class codes 22x..26x mapped to grades 8..12; last digit = parallel letter.",
        "The room of type 'gym' is the sports hall and holds several PE lessons at once.",
        "Rooms of type 'lab' are labs; 'Стая n' rooms are regular classrooms.",
        "'Първа група'/'Втора група' and language names are group labels as printed.",
    ]
    meta["known_quirks"] = [
        q.replace("'СБ'", "the sports hall") for q in meta.get("known_quirks", [])
    ]
    meta["anonymized"] = True
    return seed


def main(path: str) -> None:
    p = Path(path)
    data = anonymize(json.loads(p.read_text(encoding="utf-8")))
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    p.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main(sys.argv[1])
