"""BE-17: import the school's aSc TimeTables "export to Excel" (.xlsx).

The file is untrusted, like the FE-13 template. This module only READS it and
translates it into the template's sheets (names, not ids) plus a list of rules;
`app.importing.import_sheets` then does the real work, so every row goes through
the same forms and the whole import is one transaction.

Assumptions (no school rule is invented here):
- an aSc import REPLACES all school data, like the template. The bell times are not in
  the file, so the CURRENT periods are kept (and are required);
- „Почивка“ (teacher, class, subject) means "no lessons in these slots" and „Прозорци“ and
  <a,b> are hard limits; „Прозорци“ = 20 means no limit (nothing is created);
- an aSc lesson row is one course with one part; its classes and groups are paired by
  position ("211,212" with "Цял клас,Първа група"), a single class takes every listed group;
- „Свободни класни стаи“ are the allowed rooms. „Класни стаи на учителите“ means the rooms
  of the lesson's teachers (sheet „Списък на учители“), „…на предметите“ the rooms of its subject;
- the groups of a class come from its „Деление“; the split names (групи, езици, пол) are ours;
- room type, capacity and the current timetable (per-subject sheets) are not imported.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field

from openpyxl import load_workbook
from sqlalchemy import select

from app import models as m
from app.class_names import current_school_year_start
from app.importing import (
    CLASSES,
    COURSES,
    GROUPS,
    HEADERS,
    PERIODS,
    ROOMS,
    SUBJECTS,
    TEACHERS,
    ImportFileError,
    _check_size,
    _text,
    _week_code,
    import_sheets,
)
from app.models import db

TEACHER_S, TEACHER_ROOMS_S, CLASS_S, ROOM_S, SUBJECT_S, LESSON_S = (
    "Учители",
    "Списък на учители",
    "Класове",
    "Класни стаи",
    "Предмети",
    "Часове",
)
# sheet -> headers that must exist; the rest of the columns are used when present.
REQUIRED = {
    TEACHER_S: ("Име",),
    TEACHER_ROOMS_S: (),
    CLASS_S: ("Име на класа",),
    ROOM_S: ("Име",),
    SUBJECT_S: ("Предмет",),
    LESSON_S: ("Учител", "Клас", "Група", "Предмет", "Дължина", "Брой за седмица"),
}
MAX_UNZIPPED = (
    400 * 1024 * 1024
)  # a real export is ~190 MB: 64 timetable sheets we never read
DAYS = {"пон": 1, "вт": 2, "ср": 3, "чт": 4, "пе": 5}
LENGTHS = {"час": 1, "два часа": 2, "три часа": 3}
SPLITS = {  # canonical group -> (name, the template's "Разделяне")
    "първа група": ("Първа група", "групи"),
    "втора група": ("Втора група", "групи"),
    "момчета": ("Момчета", "пол"),
    "момичета": ("Момичета", "пол"),
    "немски": ("Немски", "езици"),
    "испански": ("Испански", "езици"),
    "китайски": ("Китайски", "езици"),
}
DEFAULT_ROOM_TYPE = "стая"
NOTES = [
    "Учебните часове (звънецът) не са във файла: запазени са сегашните.",
    (
        "Типът и капацитетът на стаите не са във файла: всички са тип „стая“ с капацитет 1. "
        "Ако в стая се провеждат няколко часа едновременно (например спортна база), "
        "задайте капацитета ѝ на страница „Стаи“."
    ),
    "Текущото разписание (листовете по предмети) не се внася.",
]


def parse_rest(text: str) -> list[tuple[int, int]]:
    """„Пон: 2-11; Пон-Пе: 1, 8-11“ -> sorted, de-duplicated [(day, period)].

    Raises ValueError for anything it does not understand (never guesses).
    """
    slots = set()
    for part in text.split(";"):
        if not part.strip():
            continue
        days, colon, periods = part.partition(":")
        if not colon:
            raise ValueError(part)
        day_set = set()
        for token in days.split(","):
            first, _, last = token.strip().casefold().partition("-")
            try:
                lo, hi = DAYS[first.strip()], DAYS[(last or first).strip()]
            except KeyError:
                raise ValueError(token) from None
            day_set |= set(range(lo, hi + 1))
        for token in periods.split(","):
            first, _, last = token.strip().partition("-")
            lo, hi = int(first), int(last or first)
            if lo < 1 or hi < lo:
                raise ValueError(token)
            slots |= {(d, p) for d in day_set for p in range(lo, hi + 1)}
    return sorted(slots)


def detect_school_year(filename: str, codes) -> tuple[int | None, str]:
    """(start year, "file" | "codes" | ""): `…25-26…` in the file name, else the newest cohort."""
    found = re.search(r"(?<!\d)(?:20)?(\d{2})[-_/](?:20)?(\d{2})(?!\d)", filename)
    if found and (int(found[1]) + 1) % 100 == int(found[2]):
        return 2000 + int(found[1]), "file"
    years = [int(c[1]) for c in codes if re.fullmatch(r"2\d\d", c)]
    # The newest cohort (largest 2Y.) is in 8th grade, see app/class_names.py.
    return (2020 + max(years), "codes") if years else (None, "")


# --- reading ---


def read_asc(data: bytes) -> dict[str, list[dict]]:
    """The six sheets we use as {sheet: [{header: text}]}; Bulgarian ImportFileError."""
    bad = ImportFileError("Файлът е повреден или не е валиден Excel файл (.xlsx).")
    sheets = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
                raise ImportFileError(
                    "Файлът е твърде голям, след като се разархивира."
                )
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        for ws in wb.worksheets:
            if ws.title in REQUIRED:
                ws.reset_dimensions()  # the export claims thousands of empty rows
                rows = [
                    [_text(c) for c in row]
                    for row in ws.iter_rows(max_col=30, values_only=True)
                ]
                while rows and not any(rows[-1]):
                    rows.pop()
                _check_size(rows)
                sheets[ws.title] = rows
        wb.close()
    except ImportFileError:
        raise
    except Exception:  # noqa: BLE001  corrupt zip/XML, bad styles, ...: whatever it is, not a 500
        raise bad from None
    out = {}
    for name, headers in REQUIRED.items():
        rows = sheets.get(name)
        if rows is None:
            if headers:
                raise ImportFileError(
                    f"Във файла липсва лист „{name}“. Това не прилича на експорт от aSc."
                )
            continue
        head = rows[0] if rows else []
        missing = [h for h in headers if h not in head]
        if missing:
            raise ImportFileError(f"Лист „{name}“: липсва колона „{missing[0]}“.")
        out[name] = [  # "#" = the row in the file, for the error messages
            {"#": n, **{h: r[i] if i < len(r) else "" for i, h in enumerate(head) if h}}
            for n, r in enumerate(rows[1:], 2)
            if any(r)
        ]
    return out


# --- translating ---


@dataclass
class Translation:
    sheets: dict[str, list[list]] = field(default_factory=dict)
    rules: list[tuple] = field(
        default_factory=list
    )  # (sheet, row, type, kind, name, params)
    skipped: Counter = field(default_factory=Counter)  # reason -> rows


def _group(token: str):
    """„първа гр.“ -> ("Първа група", "групи"); None if unknown."""
    return SPLITS.get(" ".join(token.casefold().replace("гр.", "група").split()))


def _short(t: Translation, text: str, limit: int) -> str:
    """The abbreviation, or "" when it does not fit the column (never cut: that would be wrong)."""
    if len(text) > limit:
        t.skipped["кратко име, по-дълго от позволеното (не е записано)"] += 1
        return ""
    return text


def _room_names(cell: str, known: dict[str, str]):
    """Room names of a comma list, or None if one is unknown. Several rooms may be glued together."""
    out = []
    for token in (t.strip() for t in cell.split(",")):
        if not token:
            continue
        if token in known:
            out.append(known[token])
            continue
        # shortcut: aSc writes some lists without commas ("101102"): accept it only when it
        # splits into 3-character names that all exist.
        parts = [token[i : i + 3] for i in range(0, len(token), 3)]
        if len(token) % 3 or not all(p in known for p in parts):
            return None
        out += [known[p] for p in parts]
    return out


def translate(raw, periods: list[list]) -> Translation:
    t = Translation()
    skip = t.skipped
    teachers = raw[TEACHER_S]
    t.sheets[PERIODS] = [HEADERS[PERIODS], *periods]
    t.sheets[TEACHERS] = [HEADERS[TEACHERS]] + [
        [r["Име"], _short(t, r.get("Съкращение", ""), 40)] for r in teachers
    ]
    rooms = {}  # name or abbreviation -> name
    for r in raw[ROOM_S]:
        rooms[r["Име"]] = rooms[r.get("Съкращение") or r["Име"]] = r["Име"]
    t.sheets[ROOMS] = [HEADERS[ROOMS]] + [
        [r["Име"], DEFAULT_ROOM_TYPE, 1] for r in raw[ROOM_S]
    ]
    subjects = {}  # abbreviation or name -> name
    for r in raw[SUBJECT_S]:
        subjects[r["Предмет"]] = subjects[r.get("Съкращение") or r["Предмет"]] = r[
            "Предмет"
        ]
    t.sheets[SUBJECTS] = [HEADERS[SUBJECTS]] + [
        [r["Предмет"], "", _short(t, r.get("Съкращение", ""), 20), ""]
        for r in raw[SUBJECT_S]
    ]

    teacher_rooms = {
        r["Име"]: _room_names(r.get("Обучение - Класни стаи", ""), rooms) or []
        for r in raw.get(TEACHER_ROOMS_S, ())
    }
    subject_rooms = {
        r["Предмет"]: _room_names(r.get("Класни стаи", ""), rooms) or []
        for r in raw[SUBJECT_S]
    }

    # Classes, their groups (from „Деление“) and class teachers (from „Класен“).
    class_teacher = {}
    for r in teachers:
        code = r.get("Класен", "").strip()
        if code in class_teacher:
            skip["повече от един класен ръководител на клас"] += 1
        elif code:
            class_teacher[code] = r["Име"]
    classes, group_rows = {}, []  # code -> {group token: group name}
    t.sheets[CLASSES] = [HEADERS[CLASSES]]
    for r in raw[CLASS_S]:
        code = r["Име на класа"].strip()
        t.sheets[CLASSES].append([code, "", class_teacher.get(code, "")])
        classes[code] = {}
        for token in r.get("Деление", "").split(","):
            if not token.strip() or " ".join(token.split()).casefold() == "цял клас":
                continue
            found = _group(token)
            if found is None:
                skip["непознато деление на клас"] += 1
            elif found[0] not in classes[code]:
                classes[code][found[0]] = found[1]
                group_rows.append([code, *found])
    t.sheets[GROUPS] = [HEADERS[GROUPS], *group_rows]

    # Lessons: one course with one part per row.
    t.sheets[COURSES] = [HEADERS[COURSES]]
    names = {x["Име"] for x in teachers}
    for r in raw[LESSON_S]:
        row = _lesson(r, classes, names, subjects, rooms, teacher_rooms, subject_rooms)
        if isinstance(row, str):
            skip[row] += 1
        else:
            t.sheets[COURSES].append(row)

    # Rules.
    for sheet, kind, key in (
        (TEACHER_S, "teacher", "Име"),
        (CLASS_S, "class", "Име на класа"),
        (SUBJECT_S, "subject", "Предмет"),
    ):
        for r in raw[sheet]:
            _rules(t, sheet, kind, r[key].strip(), r)
    # The import reads text only, like a file would give it.
    t.sheets = {k: [[str(c) for c in row] for row in v] for k, v in t.sheets.items()}
    return t


def _lesson(r, classes, teachers, subjects, rooms, teacher_rooms, subject_rooms):
    """One „Часове“ row -> the template's course row, or the (Bulgarian) reason to skip it."""
    text = r["Дължина"].strip().casefold()
    length = LENGTHS.get(text) or (int(text) if text.isdigit() else 0)
    count = r["Брой за седмица"].strip()
    if not length or not count.isdigit() or not int(count):
        return "непозната дължина или брой часове"
    week = r.get("Цикъл", "").strip()
    week = "" if "всички" in week.casefold() else _week_code(week)
    if week not in ("", "A", "B"):
        return "непознат цикъл на седмиците"
    subject = subjects.get(r["Предмет"].strip())
    if subject is None:
        return "непознат предмет"
    names = list(dict.fromkeys(t.strip() for t in r["Учител"].split(",")))
    if not all(x in teachers for x in names):
        return "непознат учител"
    codes = [c.strip() for c in r["Клас"].split(",")]
    tokens = [g.strip() for g in r["Група"].split(",")]
    if len(tokens) != len(codes) and len(codes) != 1:
        return "броят на класовете и групите не съвпада"
    if len(codes) == 1:
        codes = codes * len(tokens)
    refs = []
    for code, token in zip(codes, tokens, strict=True):
        if code not in classes:
            return "непознат клас"
        if " ".join(token.split()).casefold() == "цял клас":
            refs.append(code)
        elif (g := _group(token)) and g[0] in classes[code]:
            refs.append(f"{code} — {g[0]}")
        else:
            return "непозната група на клас"
    allowed = []
    for token in r.get("Свободни класни стаи", "").split(","):
        token = token.strip()
        if "на учителите" in token.casefold():
            allowed += [x for name in names for x in teacher_rooms.get(name, ())]
        elif "на предметите" in token.casefold():
            allowed += subject_rooms.get(subject, ())
        elif token:
            found = _room_names(token, rooms)
            if found is None:
                return "непозната стая"
            allowed += found
    allowed = list(dict.fromkeys(allowed))
    return [
        r["#"], "", length * int(count), ", ".join([str(length)] * int(count)), subject,
        "; ".join(dict.fromkeys(refs)), "; ".join(names),
        "да" if allowed else "не", "", "; ".join(allowed), week,
    ]  # fmt: skip


def _rules(t: Translation, sheet, kind, name, r) -> None:
    """„Почивка“ (all three sheets), „Прозорци“ and <a,b> (teachers) -> rules."""
    n = r["#"]
    slots = []
    try:
        slots = [{"day": d, "period": p} for d, p in parse_rest(r.get("Почивка", ""))]
    except ValueError:
        t.skipped["„Почивка“ във формат, който не се разпознава"] += 1
    if slots:
        t.rules.append(
            (sheet, n, f"{kind}_unavailable_slots", kind, name, {"slots": slots})
        )
    if kind != "teacher":
        return
    gaps = r.get("Прозорци", "").strip()
    if gaps.isdigit() and int(gaps) != 20:  # 20 = unlimited
        t.rules.append((sheet, n, "teacher_max_gaps", kind, name, {"max": int(gaps)}))
    both = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", r.get("<a,b> (4)", ""))
    if both:
        for type_, key, value in (
            ("teacher_min_lessons_per_day", "min", int(both[1])),
            ("teacher_max_lessons_per_day", "max", int(both[2])),
        ):
            if value >= 1:
                t.rules.append((sheet, n, type_, kind, name, {key: value}))


# --- the import ---


@dataclass
class AscResult:
    errors: list[str]
    counts: dict[str, int] = field(default_factory=dict)
    conversions: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    year: int | None = None
    year_from: str = ""  # "file" | "codes" | "" (the admin typed it)


def import_asc(
    data: bytes, filename: str, school_year_start: int | None, dry_run: bool
) -> AscResult:
    """Read, translate and import; the school year is the admin's, else detected."""
    raw = read_asc(data)
    year, year_from = school_year_start, ""
    if year is None:
        codes = [r["Име на класа"].strip() for r in raw[CLASS_S]]
        year, year_from = detect_school_year(filename, codes)
    if year is None:
        year, year_from = current_school_year_start(), "default"
    periods = [
        [p.number, f"{p.start_time:%H:%M}", f"{p.end_time:%H:%M}"]
        for p in db.session.scalars(select(m.Period).order_by(m.Period.number))
    ]
    if not periods:
        return AscResult(
            [
                "Няма учебни часове. Въведете ги от страница „Учебни часове“ и опитайте пак."
            ],
            year=year,
            year_from=year_from,
        )
    t = translate(raw, periods)
    errors, counts, conversions = import_sheets(
        t.sheets, True, year, dry_run, tuple(t.rules)
    )
    return AscResult(errors, counts, conversions, dict(t.skipped), year, year_from)
