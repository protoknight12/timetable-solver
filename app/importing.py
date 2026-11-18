"""FE-13: import school data from an Excel/CSV template, and export it in the same shape.

Trust boundary: the file is untrusted. Cells are only ever read as text (never
evaluated), every row goes through the same WTForms forms as the data pages, and
the whole import is one transaction: any error rolls everything back.

Assumptions (no school rule is invented here):
- an .xlsx import REPLACES all school data (teachers ... constraints, solve jobs;
  accounts are kept). A CSV file holds one sheet and is ADDED to the existing data;
  names that already exist count as duplicates;
- rows refer to each other by name, so a name may appear only once per sheet;
- the whole-class group is created with its class (as on the data pages);
- a class written as the school's 3-digit code (234) is converted to 10Г (BE-15; the rule
  is an unconfirmed assumption, see app/class_names.py) in the sheets Класове, Групи and
  Учебен план. The admin sees every conversion in the preview before saving.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import time

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.datastructures import MultiDict

from app import models as m
from app.class_names import class_name_from_code, current_school_year_start
from app.cli import reset_school_data
from app.constraint_forms import constraint_problem
from app.forms import (
    ClassForm,
    CourseForm,
    GroupForm,
    PartForm,
    PeriodForm,
    RoomForm,
    SubjectForm,
    TeacherForm,
    group_label,
)
from app.models import db
from app.room_types import normalize_room_type, room_type_label

# shortcut: private helpers of the data pages, reused so both paths build rows alike.
from app.routes.data import _add_whole_group, _fill_part
from app.solver_input import build_solver_input

TEACHERS, SUBJECTS, ROOMS, CLASSES, GROUPS, PERIODS, COURSES = (
    "Учители",
    "Предмети",
    "Стаи",
    "Класове",
    "Групи",
    "Учебни часове",
    "Учебен план",
)
HEADERS = {
    TEACHERS: ["Име", "Кратко име"],
    SUBJECTS: [
        "Име",
        "Тип стая по подразбиране",
        "Кратко име",
        "Цвят",
        "Изисква компютри",
        "Стаи",  # optional: room names split by SEP (SUBJ-ROOMS)
    ],
    ROOMS: ["Име", "Тип", "Едновременни часове", "Има компютри", "Места"],
    CLASSES: ["Име", "Година на обучение", "Класен ръководител", "Брой ученици"],
    GROUPS: ["Клас", "Име", "Разделяне", "Брой ученици"],
    PERIODS: ["Номер", "Начало", "Край"],
    COURSES: [
        "Код",
        "Име",
        "Часове седмично",
        "Блокове",
        "Предмет",
        "Групи",
        "Учители",
        "Изисква стая",
        "Тип стая",
        "Позволени стаи",
        "Седмица",  # last and optional, so older files keep importing
    ],
}
# Columns older files may lack (they import without them); a Subject without a color is automatic.
OPTIONAL = {
    TEACHERS: ("Кратко име",),
    SUBJECTS: ("Кратко име", "Цвят", "Изисква компютри", "Стаи"),
    ROOMS: ("Има компютри", "Места"),
    CLASSES: ("Класен ръководител", "Брой ученици"),
    GROUPS: ("Брой ученици",),
    COURSES: ("Седмица",),
}
# Cells holding a room type (a code or the Bulgarian label is accepted) and yes/no cells (BE-20).
ROOM_TYPE_COLUMNS = {
    SUBJECTS: ("Тип стая по подразбиране",),
    ROOMS: ("Тип",),
    COURSES: ("Тип стая",),
}
YES_NO_COLUMNS = {SUBJECTS: ("Изисква компютри",), ROOMS: ("Има компютри",)}
YES_WORDS, NO_WORDS = ("да", "yes", "true", "1"), ("не", "no", "false", "0", "")
CONSTRAINTS = "Ограничения"  # counted like a sheet, but only aSc files carry them
INSTRUCTIONS = "Инструкции"
INSTRUCTION_LINES = [
    "Попълнете листовете. Не променяйте имената на листовете и заглавията в първия ред.",
    "Групи: по един ред за всяка допълнителна група; целият клас се създава заедно с класа.",
    (
        "Учебен план: по един ред за всяка част. Редове с един и същ код са части на един "
        "учебен план (провеждат се едновременно) и трябва да имат еднакви име, часове и блокове."
    ),
    (
        "„Групи“, „Учители“ и „Позволени стаи“: няколко стойности се разделят с „;“. "
        "Целият клас се записва с името на класа (например 8А), група — „8А — Първа група“."
    ),
    "Блокове: например 2, 1. Изисква стая: да или не.",
    "Седмица (не е задължителна): празно или „всяка“ за всяка седмица, „A“ или „B“ за час през седмица.",
    "Учители: „Кратко име“ не е задължително. Класове: „Класен ръководител“ не е задължително (име на учител от лист „Учители“).",
    "Предмети: „Кратко име“ и „Цвят“ (например #4e79a7) не са задължителни; без цвят се избира автоматичен. „Изисква компютри“: да или не.",
    (
        "Тип стая (в листовете „Предмети“, „Стаи“ и „Учебен план“): Класна стая, Лаборатория / STEM кабинет, "
        "Лекционна зала, Физкултурен салон, Работилница или Друго (може и кодът: classroom, lab, lecture, gym, workshop, other)."
    ),
    "Стаи: „Има компютри“ (да или не) и „Места“ не са задължителни. Класове и групи: „Брой ученици“ не е задължителен.",
    "Внимание: качването на Excel файл ЗАМЕСТВА всички текущи данни.",
]
# The column of each sheet that names classes (the course sheet lists several, split by SEP).
CLASS_COLUMN = {CLASSES: "Име", GROUPS: "Клас", COURSES: "Групи"}
MAX_ROWS = 5000
MAX_UNZIPPED = 50 * 1024 * 1024  # a small .xlsx can inflate hugely (zip bomb)
SEP = ";"


class ImportFileError(Exception):
    """The file cannot be read at all; the message is Bulgarian."""


# --- reading (untrusted bytes -> {sheet: [[text, ...], ...]}) ---


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "не"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, time):
        return f"{value:%H:%M}"
    return str(value).strip()


def read_xlsx(data: bytes) -> dict[str, list[list[str]]]:
    bad = ImportFileError("Файлът е повреден или не е валиден Excel файл (.xlsx).")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
                raise ImportFileError(
                    "Файлът е твърде голям, след като се разархивира."
                )
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sheets = {}
        for ws in wb.worksheets:
            if ws.title in HEADERS:
                rows = [
                    [_text(c) for c in row]
                    for row in ws.iter_rows(max_col=20, values_only=True)
                ]
                _check_size(rows)
                sheets[ws.title] = rows
        wb.close()
    except ImportFileError:
        raise
    except Exception:  # noqa: BLE001  corrupt zip/XML, bad styles, ...: whatever it is, not a 500
        raise bad from None
    if not sheets:
        raise ImportFileError(
            "Във файла няма нито един от очакваните листове: "
            + ", ".join(HEADERS)
            + "."
        )
    return sheets


def read_csv(data: bytes, sheet: str) -> dict[str, list[list[str]]]:
    try:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1251")  # Excel in Bulgaria saves CSV this way
        first = text.splitlines()[0] if text.strip() else ""
        delimiter = max(",;\t", key=first.count)  # Excel (bg) writes ";"
        rows = [
            [_text(c) for c in row]
            for row in csv.reader(io.StringIO(text), delimiter=delimiter)
        ]
    except (csv.Error, UnicodeDecodeError):
        raise ImportFileError("Файлът не е валиден CSV файл.") from None
    _check_size(rows)
    return {sheet: rows}


def _check_size(rows):
    if len(rows) > MAX_ROWS + 1:
        raise ImportFileError(f"Един лист може да има най-много {MAX_ROWS} реда.")


# --- importing ---


def _week_code(text: str) -> str:
    """ "", "всяка" -> ""; "A"/"B", also typed with Cyrillic А/В or as "Седмица A" -> "A"/"B".

    Anything else is kept, so the course form reports it as an invalid choice.
    """
    t = text.strip().upper().removeprefix("СЕДМИЦА").strip()
    return "" if t in ("", "ВСЯКА") else t.translate({ord("А"): "A", ord("В"): "B"})


def _by_name(model) -> dict:
    return {x.name: x for x in db.session.scalars(select(model))}


def _ref(group: m.StudentGroup) -> str:
    """How the template names a group: the bare class name for the whole class."""
    return group.school_class.name if group.is_whole else group_label(group)


class _Import:
    def __init__(self, sheets, school_year_start, rules=()):
        self.rules = rules  # BE-17: constraints from an aSc file, see constraints()
        self.errors: list[str] = []
        self.counts: dict[str, int] = {}
        self.conversions: dict[str, str] = {}  # "234" -> "10Г", for the preview
        self.year = school_year_start
        self.tables = {s: self._table(s, sheets.get(s)) for s in HEADERS}

    def _table(self, sheet, raw):
        """[(row number, {header: text})]; blank rows are skipped."""
        if not raw:
            return []
        header = raw[0]
        wanted = [h for h in HEADERS[sheet] if h not in OPTIONAL.get(sheet, ())]
        missing = [h for h in wanted if h not in header]
        if missing:
            self.errors.append(
                f"Лист „{sheet}“: липсва колона „{'“, „'.join(missing)}“."
            )
            return []
        at = {h: header.index(h) for h in HEADERS[sheet] if h in header}
        rows = []
        for n, row in enumerate(raw[1:], 2):
            cells = {h: row[i] if i < len(row) else "" for h, i in at.items()}
            if any(cells.values()):
                cells = {h: self._cell(sheet, n, h, v) for h, v in cells.items()}
                if sheet in CLASS_COLUMN:
                    h = CLASS_COLUMN[sheet]
                    cells[h] = SEP.join(
                        self._class_ref(sheet, n, t) for t in cells[h].split(SEP)
                    )
                rows.append((n, cells))
        return rows

    def _cell(self, sheet, n, header, value):
        """Room types become codes, yes/no becomes what a checkbox posts ("y" or "")."""
        if header in ROOM_TYPE_COLUMNS.get(sheet, ()):
            return (
                normalize_room_type(value) or value
            )  # unknown text: the form reports it
        if header in YES_NO_COLUMNS.get(sheet, ()):
            word = value.strip().casefold()
            if word in YES_WORDS:
                return "y"
            if word not in NO_WORDS:
                self.error(sheet, n, f"{header}: Въведете „да“ или „не“.")
            return ""
        return value

    def _class_ref(self, sheet, n, token):
        """A class code ("234" or "234 — Група") becomes its standard name; other text is kept."""
        head, dash, tail = token.strip().partition(" — ")
        if not re.fullmatch(r"\d{3}", head.strip()):
            return token
        try:
            name = (
                self.conversions.get(head) or class_name_from_code(head, self.year)[1]
            )
        except ValueError as e:
            self.error(sheet, n, str(e))
            return token
        self.conversions[head] = name
        return name + dash + tail

    def error(self, sheet, n, message):
        self.errors.append(f"Лист „{sheet}“, ред {n}: {message}")

    def form(self, sheet, n, cls, data, columns, **kw):
        """Validate one row with the data pages' form; None (and errors) if invalid."""
        form = cls(formdata=MultiDict(data), meta={"csrf": False}, **kw)
        if form.validate():
            return form
        for field, messages in form.errors.items():
            self.error(sheet, n, f"{columns.get(field, field)}: {' '.join(messages)}")

    def done(self, sheet):
        self.counts[sheet] = self.counts.get(sheet, 0) + 1

    def run(self):
        self.periods()
        self.named(
            TEACHERS,
            m.Teacher,
            TeacherForm,
            {"name": "Име", "short_name": "Кратко име"},
        )
        self.named(
            ROOMS,
            m.Room,
            RoomForm,
            {
                "name": "Име",
                "type": "Тип",
                "concurrent_capacity": "Едновременни часове",
                "has_computers": "Има компютри",
                "seats": "Места",
            },
        )
        self.named(
            SUBJECTS,
            m.Subject,
            SubjectForm,
            {
                "name": "Име",
                "default_room_type": "Тип стая по подразбиране",
                "short_name": "Кратко име",
                "color": "Цвят",
                "requires_computers": "Изисква компютри",
                "room_ids": "Стаи",
            },
            prep=self._subject_rooms,
        )
        self.class_teachers()
        self.named(
            CLASSES,
            m.SchoolClass,
            ClassForm,
            {
                "name": "Име",
                "grade": "Година на обучение",
                "class_teacher_id": "Класен ръководител",
                "student_count": "Брой ученици",
            },
            after=_add_whole_group,
        )
        self.groups()
        self.courses()
        self.constraints()

    def class_teachers(self):
        """The class teacher is written as a name; the class form wants the id."""
        teachers = _by_name(m.Teacher)  # the query also flushes the new teachers
        for n, r in self.tables[CLASSES]:
            name = r.get("Класен ръководител", "")
            if not name:
                continue
            if name in teachers:
                r["Класен ръководител"] = str(teachers[name].id)
            else:
                self.error(CLASSES, n, f"Класен ръководител: Непознат учител „{name}“.")
                r["Класен ръководител"] = ""

    def constraints(self):
        """BE-17: rules read from an aSc file; confirmed, as they are the school's own."""
        if not self.rules or self.errors:
            return
        db.session.flush()
        ids = {
            "teacher": _by_name(m.Teacher),
            "subject": _by_name(m.Subject),
            "class": _by_name(m.SchoolClass),
        }
        inp = build_solver_input(db.session)
        for sheet, n, type_, kind, name, extra in self.rules:
            if kind == "class":
                name = self._class_ref(sheet, n, name)
            if name not in ids[kind]:
                self.error(sheet, n, f"Ограничение: непознат запис „{name}“.")
                continue
            params = {f"{kind}_id": ids[kind][name].id, **extra}
            data = {"type": type_, "hard": True, "weight": None, "params": params}
            _, problem = constraint_problem(data, inp)
            if problem:
                self.error(sheet, n, problem)
                continue
            db.session.add(
                m.Constraint(
                    type=type_,
                    params=params,
                    hard=True,
                    status="confirmed",
                    source="import",
                )
            )
            self.done(CONSTRAINTS)

    def periods(self):
        rows = self.tables[PERIODS]
        before = len(self.errors)
        have = set(db.session.scalars(select(m.Period.number)))
        columns = {"start_time": "Начало", "end_time": "Край"}
        for n, r in rows:
            try:
                number = int(r["Номер"])
                if number < 1:
                    raise ValueError
            except ValueError:
                self.error(PERIODS, n, "Номер: Въведете цяло число, поне 1.")
                number = None
            form = self.form(
                PERIODS,
                n,
                PeriodForm,
                {"start_time": r["Начало"], "end_time": r["Край"]},
                columns,
            )
            if number in have:
                self.error(PERIODS, n, f"Дублиран ред: час {number} вече съществува.")
            elif form and number:
                have.add(number)
                db.session.add(
                    m.Period(
                        number=number,
                        start_time=form.start_time.data,
                        end_time=form.end_time.data,
                    )
                )
                self.done(PERIODS)
        # The solver input rejects gaps in the numbering ("bad_time_grid").
        gap = sorted(have) != list(range(min(have), max(have) + 1)) if have else False
        if rows and len(self.errors) == before and gap:
            self.errors.append(
                f"Лист „{PERIODS}“: номерата на учебните часове трябва да са "
                "последователни, без пропуски."
            )

    def _subject_rooms(self, n, r):
        """Room names of a subject row -> ids (rooms are imported first); None if unknown."""
        known = _by_name(m.Room)
        ids, bad = [], False
        for token in (t.strip() for t in r.get("Стаи", "").split(SEP)):
            if not token:
                continue
            if token in known:
                ids.append(str(known[token].id))
            else:
                bad = True
                self.error(SUBJECTS, n, f"Стаи: Непозната стая „{token}“.")
        return None if bad else {"room_ids": ids}

    def named(self, sheet, model, form_cls, columns, after=None, prep=None):
        known = _by_name(model)
        for n, r in self.tables[sheet]:
            data = {f: r.get(h, "") for f, h in columns.items()}
            if prep:
                extra = prep(n, r)
                if extra is None:
                    continue
                data.update(extra)
            form = self.form(sheet, n, form_cls, data, columns)
            if form is None:
                continue
            name = form.name.data
            if name in known:
                self.error(sheet, n, f"Дублиран ред: „{name}“ вече съществува.")
                continue
            item = model()
            form.populate_obj(item)
            if after:
                after(item)
            db.session.add(item)
            known[name] = item
            self.done(sheet)

    def groups(self):
        classes = _by_name(m.SchoolClass)
        taken = {
            f"{g.school_class.name} — {g.name}"
            for c in classes.values()
            for g in c.groups
        }
        columns = {"name": "Име", "split": "Разделяне", "student_count": "Брой ученици"}
        for n, r in self.tables[GROUPS]:
            school_class = classes.get(r["Клас"])
            if school_class is None:
                self.error(GROUPS, n, f"Непознат клас „{r['Клас']}“.")
                continue
            form = self.form(
                GROUPS,
                n,
                GroupForm,
                {
                    "name": r["Име"],
                    "split": r["Разделяне"],
                    "student_count": r.get("Брой ученици", ""),
                },
                columns,
            )
            if form is None:
                continue
            label = f"{school_class.name} — {form.name.data}"
            if label in taken:
                self.error(GROUPS, n, f"Дублиран ред: „{label}“ вече съществува.")
                continue
            taken.add(label)
            school_class.groups.append(
                m.StudentGroup(
                    name=form.name.data,
                    split=form.split.data,
                    student_count=form.student_count.data,
                )
            )
            self.done(GROUPS)

    def courses(self):
        rows = self.tables[COURSES]
        if rows and self.errors:
            self.errors.append(
                f"Лист „{COURSES}“ не е проверен: първо поправете другите грешки."
            )
            return
        s = db.session
        subjects, teachers, rooms = (
            _by_name(m.Subject),
            _by_name(m.Teacher),
            _by_name(m.Room),
        )
        groups = {
            _ref(g): g for g in s.scalars(select(m.StudentGroup)) if g.school_class
        }
        course_cols = {
            "label": "Име",
            "periods_per_week": "Часове седмично",
            "block_sizes": "Блокове",
            "week": "Седмица",
        }
        part_cols = {
            "subject": "Предмет",
            "groups": "Групи",
            "teachers": "Учители",
            "needs_room": "Изисква стая",
            "room_type": "Тип стая",
            "allowed_room_ids": "Позволени стаи",
        }
        seen: dict[str, tuple] = {}  # code -> (course or None if invalid, its cells)
        for n, r in rows:
            if not r["Код"]:
                self.error(COURSES, n, "Код: Полето е задължително.")
                continue
            r["Седмица"] = _week_code(r.get("Седмица", ""))
            cells = tuple(r[h] for h in course_cols.values())
            if r["Код"] not in seen:
                form = self.form(
                    COURSES,
                    n,
                    CourseForm,
                    {f: r[h] for f, h in course_cols.items()},
                    course_cols,
                )
                course = None
                if form:
                    course = m.Course()
                    form.populate_obj(course)
                    s.add(course)
                seen[r["Код"]] = (course, cells)
            course, first = seen[r["Код"]]
            if cells != first:
                self.error(
                    COURSES,
                    n,
                    "Редовете с един и същ код трябва да имат еднакви име, "
                    "часове и блокове.",
                )
                continue
            part_cells = [r[h] for h in part_cols.values()]
            if course is None or not any(part_cells):
                continue  # invalid course (already reported) or a course without parts
            if self.part(n, course, r, subjects, teachers, rooms, groups, part_cols):
                self.done(COURSES)

    def part(self, n, course, r, subjects, teachers, rooms, groups, columns):
        bad = False

        def many(header, known, unknown):
            nonlocal bad
            ids = []
            for token in (t.strip() for t in r[header].split(SEP)):
                if not token:
                    continue
                if token in known:
                    ids.append(str(known[token].id))
                else:
                    bad = True
                    self.error(COURSES, n, f"{header}: {unknown} „{token}“.")
            return ids

        db.session.flush()  # new rows need their ids
        subject = subjects.get(r["Предмет"])
        if r["Предмет"] and subject is None:
            bad = True
            self.error(COURSES, n, f"Предмет: Непознат предмет „{r['Предмет']}“.")
        group_ids = many("Групи", groups, "Непознат клас или група")
        teacher_ids = many("Учители", teachers, "Непознат учител")
        room_ids = many("Позволени стаи", rooms, "Непозната стая")
        needs = r["Изисква стая"].casefold()
        if needs not in ("да", "не", ""):
            bad = True
            self.error(COURSES, n, "Изисква стая: Въведете „да“ или „не“.")
        if bad:
            return False
        data = {
            "subject": str(subject.id) if subject else "",
            "groups": group_ids,
            "teachers": teacher_ids,
            "room_type": r["Тип стая"],
            "allowed_room_ids": room_ids,
        }
        if needs == "да":
            data["needs_room"] = "y"
        form = self.form(COURSES, n, PartForm, data, columns, course=course)
        if form is None:
            return False
        part = m.CoursePart()
        with db.session.no_autoflush:  # the part is not complete yet
            _fill_part(part, form)
        part.course = course
        db.session.add(part)
        return True


def import_sheets(
    sheets: dict[str, list[list[str]]],
    replace: bool,
    school_year_start: int | None = None,
    dry_run: bool = False,
    rules: tuple = (),
) -> tuple[list[str], dict[str, int], dict[str, str]]:
    """Import all or nothing (dry_run: check, then roll back).

    Returns (errors, rows imported per sheet, class code -> name conversions).
    """
    run = _Import(sheets, school_year_start or current_school_year_start(), rules)
    if not run.errors and not any(run.tables.values()):
        run.errors.append("Във файла няма данни за импорт.")
    if run.errors:
        return run.errors, {}, run.conversions
    try:
        if replace:
            reset_school_data()
        run.run()
        if not run.errors and not dry_run:
            db.session.commit()
            return [], run.counts, run.conversions
        if not run.errors:
            db.session.rollback()
            return [], run.counts, run.conversions
    except SQLAlchemyError:
        run.errors.append("Данните не бяха запазени: те влизат в конфликт.")
    db.session.rollback()
    return run.errors, {}, run.conversions


# --- export / template ---


def _type_cell(code: str | None) -> str:
    return room_type_label(code) if code else ""


def _yes_no(value: bool) -> str:
    return "да" if value else "не"


def build_workbook(with_data: bool) -> Workbook:
    wb = Workbook()
    wb.remove(wb.active)
    s = db.session.scalars
    rows = {sheet: [] for sheet in HEADERS}
    if with_data:
        rows[TEACHERS] = [
            [t.name, t.short_name or ""]
            for t in s(select(m.Teacher).order_by(m.Teacher.id))
        ]
        rows[SUBJECTS] = [
            [
                x.name,
                _type_cell(x.default_room_type),
                x.short_name or "",
                x.color or "",
                _yes_no(x.requires_computers),
                f"{SEP} ".join(r.name for r in x.rooms),
            ]
            for x in s(select(m.Subject).order_by(m.Subject.id))
        ]
        rows[ROOMS] = [
            [
                x.name,
                _type_cell(x.type),
                x.concurrent_capacity,
                _yes_no(x.has_computers),
                x.seats or "",
            ]
            for x in s(select(m.Room).order_by(m.Room.id))
        ]
        for c in s(select(m.SchoolClass).order_by(m.SchoolClass.id)):
            teacher = c.class_teacher.name if c.class_teacher else ""
            rows[CLASSES].append(
                [c.name, c.grade or "", teacher, c.student_count or ""]
            )
            rows[GROUPS] += [
                [c.name, g.name, g.split, g.student_count or ""]
                for g in sorted(c.groups, key=lambda g: g.id)
                if not g.is_whole
            ]
        rows[PERIODS] = [
            [p.number, f"{p.start_time:%H:%M}", f"{p.end_time:%H:%M}"]
            for p in s(select(m.Period).order_by(m.Period.number))
        ]
        room_names = {r.id: r.name for r in s(select(m.Room))}
        for c in s(select(m.Course).order_by(m.Course.id)):
            head = [
                c.id,
                c.label,
                c.periods_per_week,
                ", ".join(map(str, c.block_sizes)),
            ]
            parts = sorted(c.parts, key=lambda p: p.id)
            rows[COURSES] += [
                head
                + [
                    p.subject.name,
                    f"{SEP} ".join(_ref(g) for g in p.groups),
                    f"{SEP} ".join(t.name for t in p.teachers),
                    "да" if p.needs_room else "не",
                    _type_cell(p.room_type),
                    f"{SEP} ".join(room_names[i] for i in p.allowed_room_ids or []),
                    c.week or "",
                ]
                for p in parts
            ] or [head + [""] * 6 + [c.week or ""]]
    for sheet, headers in HEADERS.items():
        ws = wb.create_sheet(sheet)
        ws.append(headers)
        for row in rows[sheet]:
            ws.append(row)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"  # a name like "=1+1" must not become a formula
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 24
        ws.freeze_panes = "A2"
    ws = wb.create_sheet(INSTRUCTIONS)
    for line in INSTRUCTION_LINES:
        ws.append([line])
    ws.column_dimensions["A"].width = 120
    return wb
