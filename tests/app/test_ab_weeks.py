"""BE-18: alternating A/B weeks in the course form, import, grid, exports, workload, editing."""

from datetime import time
from io import BytesIO

from openpyxl import load_workbook
from sqlalchemy import select

from app import models as m
from app.models import db
from app.routes.workload import compute
from app.solver_input import build_solver_input
from tests.app.test_import import BASE, COURSES, upload, xlsx

# (subject, week, day, start period, length); one teacher, one class, same slot for A and B.
LESSONS = (
    ("Математика", None, 1, 1, 1),
    ("Физика", "A", 1, 2, 1),
    ("Химия", "B", 1, 2, 1),
)


def _school(lessons=LESSONS):
    ivan = m.Teacher(name="Иван Иванов")
    cls = m.SchoolClass(name="5А", grade=5)
    cls.groups.append(m.StudentGroup(name="", is_whole=True))
    db.session.add_all([ivan, cls])
    db.session.add_all(
        m.Period(number=n, start_time=time(7 + n, 0), end_time=time(7 + n, 45))
        for n in (1, 2, 3)
    )
    job = m.SolveJob(
        status="finished", solver_status="OPTIMAL", published_at=db.func.now()
    )
    db.session.add(job)
    db.session.flush()
    for name, week, day, start, length in lessons:
        course = m.Course(periods_per_week=length, block_sizes=[length], week=week)
        course.parts.append(
            m.CoursePart(
                subject=m.Subject(name=name), teachers=[ivan], groups=cls.groups
            )
        )
        db.session.add(course)
        db.session.flush()
        db.session.add(
            m.Lesson(
                job_id=job.id,
                course_id=course.id,
                part_id=course.parts[0].id,
                day=day,
                start_period=start,
                length=length,
            )
        )
    db.session.commit()
    return job


def test_solver_input_carries_the_week(migrated):
    _school()
    inp = build_solver_input(db.session)
    assert [c.week for c in inp.courses] == [None, "A", "B"]
    assert inp.problems() == []


def test_course_form_saves_and_shows_the_week(make_client, migrated):
    admin = make_client("admin")
    form = {"periods_per_week": "2", "block_sizes": "2"}
    assert (
        admin.post("/data/courses/new", data={**form, "week": "A"}).status_code == 302
    )
    assert admin.post("/data/courses/new", data={**form, "week": ""}).status_code == 302
    assert (
        admin.post("/data/courses/new", data=form).status_code == 302
    )  # field missing
    weeks = db.session.scalars(select(m.Course.week).order_by(m.Course.id)).all()
    assert weeks == ["A", None, None]
    first = db.session.scalars(select(m.Course).order_by(m.Course.id)).first()
    edit = admin.get(f"/data/courses/{first.id}/edit").text
    assert '<option selected value="A">' in edit and "Седмица" in edit
    assert "само A" in admin.get("/data/courses/").text


def test_course_form_rejects_another_week(make_client, migrated):
    r = make_client("admin").post(
        "/data/courses/new",
        data={"periods_per_week": "2", "block_sizes": "2", "week": "C"},
    )
    assert r.status_code == 200 and "Невалиден избор." in r.text
    assert db.session.scalar(select(db.func.count()).select_from(m.Course)) == 0


def test_import_reads_the_optional_week_column(make_client, migrated):
    sheets = {k: [list(r) for r in v] for k, v in BASE.items()}
    sheets[COURSES][0] = sheets[COURSES][0][:10] + ["Седмица"]  # the new last column
    row = sheets[COURSES][1]
    sheets[COURSES] = [sheets[COURSES][0], row + ["А"]]  # Cyrillic letter, as typed
    admin = make_client("admin")
    assert upload(admin, xlsx(sheets)).status_code == 302
    assert db.session.scalar(select(m.Course.week)) == "A"
    # export writes it back; an older file without the column still imports (BASE).
    wb = load_workbook(BytesIO(admin.get("/data/import/export.xlsx").data))
    assert wb[COURSES]["K1"].value == "Седмица" and wb[COURSES]["K2"].value == "A"
    assert upload(admin, xlsx(BASE)).status_code == 302
    assert db.session.scalar(select(m.Course.week)) is None


def test_grid_marks_the_week_and_filters_it(make_client, migrated):
    _school()
    client = make_client("admin")
    both = client.get("/timetable").text
    assert "Седмица A" in both and "Седмица B" in both
    only_a = client.get("/timetable?week=A").text
    assert "Физика" in only_a and "Математика" in only_a and "Химия" not in only_a
    assert "Химия" in client.get("/timetable?week=B").text
    assert "Двете седмици" in both


def test_excel_and_overview_mark_the_week_in_the_cell_text(make_client, migrated):
    _school()
    client = make_client("admin")
    ws = load_workbook(BytesIO(client.get("/timetable.xlsx").data))["5А"]
    assert ws["B3"].value.startswith("Физика (A)\n") and "Химия (B)" in ws["B3"].value
    assert not ws["B2"].value.startswith("Математика (")
    overview = load_workbook(BytesIO(client.get("/timetable.xlsx").data))["Общ разпис"]
    assert any("(A)" in str(c.value) for row in overview.iter_rows() for c in row)
    html = client.get("/overview").text
    assert "(A)" in html and "(B)" in html


def test_workload_shows_both_weeks_when_they_differ(migrated):
    # Mon: every-week at 1, A-only at 3 (a gap in week A only), B-only at 2.
    job = _school(
        (
            ("Математика", None, 1, 1, 1),
            ("Физика", "A", 1, 3, 1),
            ("Химия", "B", 1, 2, 1),
        )
    )
    r = next(iter(compute(job.id).values()))
    assert r["periods"] == 2 and r["periods_text"] == 2  # A: 2 periods, B: 2 periods
    assert r["gaps"] == 1  # week A has periods 1 and 3
    assert r["subjects"] == {
        "Математика": 1,
        "Физика": "A: 1 / B: 0",
        "Химия": "A: 0 / B: 1",
    }


def test_workload_periods_per_week_differ_between_weeks(migrated):
    job = _school((("Математика", None, 1, 1, 1), ("Физика", "A", 1, 2, 2)))
    r = next(iter(compute(job.id).values()))
    assert r["periods_text"] == "A: 3 / B: 1" and r["periods"] == 3


def test_manual_move_is_checked_with_the_week_rule(make_client, migrated):
    job = _school()
    admin = make_client("admin")
    by_subject = {}
    for les in db.session.scalars(select(m.Lesson).where(m.Lesson.job_id == job.id)):
        part = db.session.get(m.CoursePart, les.part_id)
        by_subject[part.subject.name] = les
    # B next to an every-week lesson of the same teacher and class: a conflict to confirm.
    r = admin.post(
        f"/lessons/{by_subject['Химия'].id}/move", data={"day": 1, "start_period": 1}
    )
    assert r.status_code == 200 and "Запази въпреки това" in r.text
    # A and B already share slot 2; moving A to a free slot is clean.
    r = admin.post(
        f"/lessons/{by_subject['Физика'].id}/move", data={"day": 1, "start_period": 3}
    )
    assert r.status_code == 302
