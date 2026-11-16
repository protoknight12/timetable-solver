"""BE-17: import of the school's aSc Excel export. The workbook is built here from fake values."""

from collections import Counter
from datetime import time
from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from app import models as m
from app.asc_import import detect_school_year, parse_rest, read_asc
from app.cli import load_school
from app.importing import build_workbook, import_sheets, read_xlsx
from app.jobs import run_solve
from app.models import db
from app.routes.solve import short_teacher
from tools.seed_to_contract import SEED, load_seed

PAGE = "/data/import/asc"
SAME = "Цял клас"

SHEETS = {
    "Учители": [
        [None, "Име", "Съкращение", "Класен", "Прозорци", "<a,b> (4)", "Почивка"],
        [None, "Тест Първи", "Т. Първи", "251  ", "2", " 1 - 4", "Пон: 5; Вт-Пе: 4-5"],
        [None, "Тест Втори", "Т. Втори", None, "20", "-", None],
        [None, "Тест Трети", "Т. Трети", None, "20", "-", None],
    ],
    "Списък на учители": [
        [None, "Име", "Обучение - Класни стаи"],
        [None, "Тест Първи", "101"],
        [None, "Тест Втори", "101,102"],
        [None, "Тест Трети", ""],
    ],
    "Класове": [
        ["Име на класа", "Съкращение", "Почивка", "Деление"],
        ["251", "251", None, "Цял клас,Първа група , втора гр.,Немски,r"],
        ["252", "252", "Пон: 4-5", "Цял клас"],
    ],
    "Класни стаи": [
        ["Име", "Съкращение", "Вид"],
        ["101", "101", None],
        ["102", "102", None],
    ],
    "Предмети": [
        [None, "Предмет", "Съкращение", "Класни стаи", "Почивка"],
        [None, "Математика", "Мат", None, None],
        [None, "Физическо възпитание", "ФВС", "102", "Ср: 1"],
    ],
    "Часове": [
        [
            "Учител",
            "Клас",
            "Група",
            "Предмет",
            "Дължина",
            "Брой за седмица",
            "Свободни класни стаи",
            "Цикъл",
        ],
        # row 2: whole class, one double lesson, in the teacher's room
        [
            "Тест Първи",
            "251",
            SAME,
            "Мат",
            "Два часа",
            1,
            "Класни стаи на учителите",
            " Всички седмици",
        ],
        # row 3: a group, two single lessons, every second week
        ["Тест Първи", "251", "Първа група ", "Мат", "Час", 2, "", " Седмица A"],
        # row 4: joint lesson of two classes with two co-teachers
        [
            "Тест Втори,Тест Трети",
            "251,252",
            f"{SAME},{SAME}",
            "Мат",
            "Час",
            1,
            "",
            " Седмица B",
        ],
        # row 5: the subject's own room
        [
            "Тест Втори",
            "252",
            SAME,
            "ФВС",
            "Час",
            1,
            "Класни стаи на предметите",
            " Всички седмици",
        ],
        # rows 6-8: skipped
        ["Тест Втори", "299", SAME, "Мат", "Час", 1, "", " Всички седмици"],
        ["Тест Втори", "252", SAME, "???", "Час", 1, "", " Всички седмици"],
        ["Тест Втори", "252", "Втора група", "Мат", "Час", 1, "", " Всички седмици"],
    ],
}


def xlsx(sheets=None) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in (sheets or SHEETS).items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    ws = wb.create_sheet("Математика.")  # a per-subject timetable sheet: ignored
    ws.append(["Пон", "1"])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def periods(n=5):
    db.session.add_all(
        m.Period(number=i, start_time=time(7 + i, 0), end_time=time(7 + i, 45))
        for i in range(1, n + 1)
    )
    db.session.commit()


def upload(client, data=None, name="school_25-26.xlsx", **form):
    form = {"asc-confirm": "y", **form}
    return client.post(
        PAGE,
        data={"asc-file": (BytesIO(data or xlsx()), name), **form},
        content_type="multipart/form-data",
    )


def count(model):
    return db.session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", []),
        ("Пон: 2-3", [(1, 2), (1, 3)]),
        ("Вт: 1; Ср: 2", [(2, 1), (3, 2)]),
        ("Чт, Пе: 4", [(4, 4), (5, 4)]),
        # ranges of days, comma lists, duplicates are de-duplicated
        ("Пон-Вт: 1, 3-4; Пон: 1", [(1, 1), (1, 3), (1, 4), (2, 1), (2, 3), (2, 4)]),
    ],
)
def test_parse_rest(text, expected):
    assert parse_rest(text) == expected


@pytest.mark.parametrize("text", ["Съб: 1", "Пон 1", "Пон: 5-3", "Пон: x", "Пон: 0"])
def test_parse_rest_rejects_what_it_does_not_understand(text):
    with pytest.raises(ValueError):
        parse_rest(text)


def test_school_year_from_the_file_name_else_from_the_codes():
    assert detect_school_year("Школа_25-26.xlsx", ["251"]) == (2025, "file")
    assert detect_school_year("rasp 2026-27.xlsx", ["251"]) == (2026, "file")
    # "12-34" is not a school year; the newest cohort (25x) is in 8th grade
    assert detect_school_year("x12-34.xlsx", ["211", "256", "x"]) == (2025, "codes")
    assert detect_school_year("x.xlsx", ["abc"]) == (None, "")


def test_import_maps_the_school(make_client):
    periods()
    r = upload(make_client("admin"))
    assert r.status_code == 302
    s = db.session
    teacher = s.scalar(select(m.Teacher).where(m.Teacher.name == "Тест Първи"))
    assert teacher.short_name == "Т. Първи"
    assert {c.name for c in s.scalars(select(m.SchoolClass))} == {"8А", "8Б"}
    eight_a = s.scalar(select(m.SchoolClass).where(m.SchoolClass.name == "8А"))
    assert eight_a.class_teacher == teacher
    # "Цял клас" + two groups + a language; the unknown "r" is dropped
    assert {g.name for g in eight_a.groups} == {
        "цял клас",
        "Първа група",
        "Втора група",
        "Немски",
    }
    assert {x.name: x.short_name for x in s.scalars(select(m.Subject))}[
        "Математика"
    ] == "Мат"
    assert count(m.Course) == 4  # 3 rows were skipped
    assert count(m.Period) == 5  # kept
    rooms = {r.name: r for r in s.scalars(select(m.Room))}
    assert (rooms["101"].type, rooms["101"].concurrent_capacity) == ("стая", 1)

    courses = {c.id: c for c in s.scalars(select(m.Course))}
    double, singles, joint, gym = sorted(courses.values(), key=lambda c: c.id)
    assert (double.periods_per_week, double.block_sizes, double.week) == (2, [2], None)
    (part,) = double.parts
    assert part.needs_room and part.allowed_room_ids == [
        rooms["101"].id
    ]  # the teacher's room
    assert (singles.periods_per_week, singles.block_sizes, singles.week) == (
        2,
        [1, 1],
        "A",
    )
    assert [g.name for g in singles.parts[0].groups] == ["Първа група"]
    (part,) = joint.parts  # joint lesson: one part, two classes, two co-teachers
    assert joint.week == "B"
    assert len(part.groups) == 2 and len(part.teachers) == 2
    assert gym.parts[0].allowed_room_ids == [rooms["102"].id]  # the subject's room

    rules = s.scalars(select(m.Constraint).order_by(m.Constraint.id)).all()
    assert Counter(c.type for c in rules) == Counter(
        teacher_unavailable_slots=1,
        teacher_max_gaps=1,  # 20 = unlimited creates nothing
        teacher_min_lessons_per_day=1,
        teacher_max_lessons_per_day=1,
        class_unavailable_slots=1,
        subject_unavailable_slots=1,
    )
    assert {(c.status, c.source, c.hard) for c in rules} == {
        ("confirmed", "import", True)
    }
    unavailable = next(c for c in rules if c.type == "teacher_unavailable_slots")
    assert len(unavailable.params["slots"]) == 1 + 4 * 2


def test_preview_shows_year_summary_and_saves_nothing(make_client):
    periods()
    admin = make_client("admin")
    r = upload(admin, **{"asc-preview": "y", "asc-confirm": ""})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and count(m.Teacher) == 0
    assert "2025/26" in html and "от името на файла" in html
    assert 'value="2025"' in html  # the field shows the year that was used
    assert (
        "251 → 8А" in html
        and "Пропуснати редове: 4" in html
        and "непознато деление на клас: 1" in html
    )
    assert "непознат клас: 1" in html and "непознат предмет: 1" in html
    assert "непозната група на клас: 1" in html and "Ограничения: 6" in html
    # no year in the name: it is inferred from the codes (newest cohort = 8th grade)
    r = upload(admin, name="school.xlsx", **{"asc-preview": "y", "asc-confirm": ""})
    assert "от кодовете на класовете" in r.get_data(as_text=True)
    # the admin's own year wins; 25x are 7th grade in 2024/25
    r = upload(admin, **{"asc-school_year": "2024", "asc-preview": "y"})
    assert "не съответства на 8.–12. клас" in r.get_data(as_text=True)


def test_replace_needs_confirm_and_periods(make_client):
    admin = make_client("admin")
    assert "Няма учебни часове" in upload(admin).get_data(as_text=True)
    periods()
    r = upload(admin, **{"asc-confirm": ""})
    assert "Потвърдете замяната" in r.get_data(as_text=True) and count(m.Teacher) == 0


def test_an_error_rolls_everything_back(make_client):
    load_school(load_seed(SEED.parent / "tiny_school.json"))
    db.session.commit()
    before = (count(m.Teacher), count(m.Course), count(m.Constraint))
    bad = {**SHEETS, "Класове": [*SHEETS["Класове"], ["253", "253", "Пон: 99", ""]]}
    r = upload(make_client("admin"), xlsx(bad))
    html = r.get_data(as_text=True)
    assert (
        "Нищо не е запазено" in html
        and "не съответства на данните на училището" in html
    )
    assert (count(m.Teacher), count(m.Course), count(m.Constraint)) == before
    assert db.session.scalar(select(m.Teacher.name)) != "Тест Първи"


def test_unreadable_files_give_bulgarian_errors(make_client):
    admin = make_client("admin")
    assert "повреден" in upload(admin, b"not a zip").get_data(as_text=True)
    only_one = {"Учители": SHEETS["Учители"]}
    assert "липсва лист" in upload(admin, xlsx(only_one)).get_data(as_text=True)
    renamed = {**SHEETS, "Часове": [["Учител", "Клас"]]}
    assert "липсва колона" in upload(admin, xlsx(renamed)).get_data(as_text=True)
    assert "само файлове .xlsx" in upload(admin, name="a.csv").get_data(as_text=True)
    with pytest.raises(Exception):  # noqa: B017  not a workbook at all
        read_asc(b"")


def test_imported_data_solves_with_no_hard_violations(make_client):
    periods()
    upload(make_client("admin"))
    job = m.SolveJob(status="queued", time_limit_s=20)
    db.session.add(job)
    db.session.commit()
    run_solve(job.id)
    job = db.session.get(m.SolveJob, job.id)
    assert (job.status, job.solver_status) in {
        ("finished", "OPTIMAL"),
        ("finished", "FEASIBLE"),
    }
    hard = db.session.scalar(
        select(func.count())
        .select_from(m.Violation)
        .where(m.Violation.job_id == job.id, m.Violation.hard.is_(True))
    )
    assert hard == 0 and count(m.Lesson) > 0


def test_short_names_class_teacher_and_the_template(make_client):
    periods()
    admin = make_client("admin")
    upload(admin)
    assert "Т. Първи" in admin.get("/data/teachers/").get_data(as_text=True)
    assert "Тест Първи" in admin.get("/data/classes/").get_data(as_text=True)
    # the FE-13 template carries both columns and imports them back
    buf = BytesIO()
    build_workbook(True).save(buf)
    errors, _, _ = import_sheets(read_xlsx(buf.getvalue()), replace=True)
    assert errors == []
    eight_a = db.session.scalar(select(m.SchoolClass).where(m.SchoolClass.name == "8А"))
    assert eight_a.class_teacher.name == "Тест Първи"
    assert eight_a.class_teacher.short_name == "Т. Първи"
    # editing a class on the data page keeps and changes the class teacher
    other = db.session.scalar(select(m.Teacher).where(m.Teacher.name == "Тест Втори"))
    r = admin.post(
        f"/data/classes/{eight_a.id}/edit",
        data={"name": "8А", "grade": "8", "class_teacher_id": str(other.id)},
    )
    assert r.status_code == 302
    db.session.refresh(eight_a)
    assert eight_a.class_teacher == other


def test_short_teacher_prefers_the_saved_short_name():
    t = SimpleNamespace(name="Иван Петров", short_name=None)
    assert short_teacher(t) == "И. Петров"
    assert (
        short_teacher(SimpleNamespace(name="Иван Петров", short_name="Пет.")) == "Пет."
    )


def test_asc_upload_is_admin_only_and_needs_csrf(make_client, migrated):
    assert make_client("viewer").post(PAGE).status_code == 403
    r = migrated.test_client().post(PAGE)
    assert r.status_code == 302 and "/login" in r.headers["Location"]
