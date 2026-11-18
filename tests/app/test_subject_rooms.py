"""SUBJ-ROOMS: rooms tied to a subject, and the solver respects them."""

from io import BytesIO

import pytest
import sqlalchemy as sa
from flask_migrate import downgrade, upgrade
from openpyxl import Workbook, load_workbook
from sqlalchemy import func, select

from app import models as m
from app.importing import HEADERS, SUBJECTS, build_workbook
from app.jobs import run_solve
from app.models import db
from app.solver_input import build_solver_input
from tests.app.test_import import snapshot, upload


@pytest.fixture
def admin(make_client):
    return make_client("admin")


def _add(*rows):
    db.session.add_all(rows)
    db.session.commit()


def _school(n_rooms=3, subject_rooms=(0, 1), seats=30, computers=False):
    """One class, one teacher, two subjects, n rooms; subject S is tied to some rooms."""
    teacher = m.Teacher(name="Т")
    k = m.SchoolClass(name="10А", grade=10, student_count=20)
    whole = m.StudentGroup(name="цял клас", is_whole=True, school_class=k)
    rooms = [
        m.Room(
            name=f"Стая {i}",
            type="classroom",
            concurrent_capacity=1,
            has_computers=computers,
            seats=seats,
        )
        for i in range(n_rooms)
    ]
    s = m.Subject(name="S", rooms=[rooms[i] for i in subject_rooms])
    other = m.Subject(name="Друг")
    _add(teacher, k, whole, s, other, *rooms)
    _add(
        *(
            m.Period(number=i, start_time=_t(8 + i), end_time=_t(8 + i, 40))
            for i in (1, 2, 3, 4)
        )
    )
    course = m.Course(periods_per_week=3, block_sizes=[1, 1, 1], label="C")
    _add(course)
    part = m.CoursePart(
        course_id=course.id,
        subject_id=s.id,
        needs_room=False,
        groups=[whole],
        teachers=[teacher],
    )
    _add(part)
    return part, rooms, s


def _t(h, mi=0):
    from datetime import time

    return time(h, mi)


# --- model + migration ---


def test_migration_creates_table_and_cascades(migrated):
    _part, rooms, s = _school()
    assert [r.id for r in s.rooms] == [rooms[0].id, rooms[1].id]
    db.session.delete(rooms[0])  # room delete only removes the link
    db.session.commit()
    db.session.expire_all()
    assert [r.id for r in db.session.get(m.Subject, s.id).rooms] == [rooms[1].id]
    assert db.session.scalar(select(func.count()).select_from(m.Subject)) == 2
    link = m.subject_rooms
    assert db.session.scalar(select(func.count()).select_from(link)) == 1
    # a subject without parts can be deleted too
    other = db.session.scalar(select(m.Subject).where(m.Subject.name == "Друг"))
    other.rooms = [rooms[2]]
    db.session.commit()
    db.session.delete(other)
    db.session.commit()
    assert db.session.scalar(select(func.count()).select_from(link)) == 1


def test_migration_up_and_down(app):
    upgrade(directory="migrations", revision="b50e3f4a5b6c")
    assert "subject_rooms" not in sa.inspect(db.engine).get_table_names()
    upgrade(directory="migrations")
    cols = sa.inspect(db.engine).get_columns("subject_rooms")
    assert {c["name"] for c in cols} == {"subject_id", "room_id"}
    downgrade(directory="migrations", revision="b50e3f4a5b6c")
    assert "subject_rooms" not in sa.inspect(db.engine).get_table_names()


# --- solver_input semantics ---


def test_semantics_own_list_wins_then_subject_then_any(migrated):
    part, rooms, s = _school()
    ids = [r.id for r in rooms]

    def part_input():
        return build_solver_input(db.session).courses[0].parts[0]

    p = part_input()
    # subject rooms apply and imply a room, although the part said needs_room=False
    assert p.needs_room is True and p.allowed_room_ids == (ids[0], ids[1])
    # the part's own list is more specific and wins
    part.needs_room, part.allowed_room_ids = True, [ids[2]]
    db.session.commit()
    assert part_input().allowed_room_ids == (ids[2],)
    # nothing on the part: the subject's rooms again
    part.allowed_room_ids = None
    db.session.commit()
    assert part_input().allowed_room_ids == (ids[0], ids[1])
    # no rooms on the subject: any room (None); needs_room follows the part
    s.rooms = []
    part.needs_room = False
    db.session.commit()
    p = part_input()
    assert p.needs_room is False and p.allowed_room_ids is None
    part.needs_room = True
    db.session.commit()
    p = part_input()
    assert p.needs_room is True and p.allowed_room_ids is None


# --- end to end ---


def _solve():
    job = m.SolveJob(status="queued", time_limit_s=20)
    _add(job)
    run_solve(job.id)
    return db.session.get(m.SolveJob, job.id)


def test_solve_respects_subject_rooms_and_checker_is_clean(migrated):
    _part, rooms, _s = _school()
    allowed = {rooms[0].id, rooms[1].id}
    job = _solve()
    assert job.status == "finished" and job.solver_status in ("OPTIMAL", "FEASIBLE")
    lessons = db.session.scalars(
        select(m.Lesson).where(m.Lesson.job_id == job.id)
    ).all()
    assert lessons and all(x.room_id in allowed for x in lessons)
    hard = db.session.scalar(
        select(func.count()).where(m.Violation.job_id == job.id, m.Violation.hard)
    )
    assert hard == 0


def test_subject_rooms_too_small_are_refused(migrated):
    _part, _rooms, _s = _school(seats=10)  # class has 20 students
    inp = build_solver_input(db.session)
    assert [p.code for p in inp.problems()] == ["no_matching_room"]
    job = _solve()
    assert job.status == "failed" and "Няма подходяща стая за: S (10А)" in job.error


def test_subject_rooms_without_computers_are_refused(migrated):
    _part, rooms, s = _school()
    s.requires_computers = True  # none of the subject's rooms has computers
    rooms[2].has_computers = True  # a computer room exists, but not for this subject
    db.session.commit()
    assert [p.code for p in build_solver_input(db.session).problems()] == [
        "no_matching_room"
    ]
    assert _solve().status == "failed"


# --- forms ---


def _room_ids(admin_rooms=("А", "Б", "В")):
    return [
        db.session.scalar(select(m.Room).where(m.Room.name == n)).id
        for n in admin_rooms
    ]


@pytest.fixture
def three_rooms(admin):
    for name, computers in (("А", ""), ("Б", ""), ("В", "y")):
        data = {"name": name, "type": "classroom", "concurrent_capacity": "1"}
        if computers:
            data["has_computers"] = computers
        admin.post("/data/rooms/new", data=data)
    return admin


def _subject(name):
    return db.session.scalar(select(m.Subject).where(m.Subject.name == name))


def test_subject_form_saves_and_edits_rooms(three_rooms):
    admin = three_rooms
    a, b, v = _room_ids()
    html = admin.get("/data/subjects/new").get_data(as_text=True)
    assert "Стаи за предмета" in html and "Ако не изберете нито една" in html
    assert f'name="room_ids" id="room_ids-{a}"' in html
    r = admin.post("/data/subjects/new", data={"name": "Инф", "room_ids": [a, v]})
    assert r.status_code == 302
    assert [x.id for x in _subject("Инф").rooms] == [a, v]
    sid = _subject("Инф").id
    page = admin.get(f"/data/subjects/{sid}/edit").get_data(as_text=True)
    assert f'id="room_ids-{a}"' in page and page.count(" checked") >= 2
    admin.post(f"/data/subjects/{sid}/edit", data={"name": "Инф", "room_ids": [b]})
    db.session.expire_all()
    assert [x.id for x in _subject("Инф").rooms] == [b]
    admin.post(f"/data/subjects/{sid}/edit", data={"name": "Инф"})  # none = any
    db.session.expire_all()
    assert _subject("Инф").rooms == []
    # list shows names, or "Всички"
    admin.post("/data/subjects/new", data={"name": "Ж", "room_ids": [a, b]})
    page = admin.get("/data/subjects/").get_data(as_text=True)
    assert "А, Б" in page and "Всички" in page
    # unknown room id is refused
    r = admin.post("/data/subjects/new", data={"name": "Х", "room_ids": [9999]})
    assert r.status_code == 200 and _subject("Х") is None


def test_subject_form_refuses_computers_without_a_computer_room(three_rooms):
    admin = three_rooms
    a, b, v = _room_ids()
    data = {"name": "К", "requires_computers": "y"}
    r = admin.post("/data/subjects/new", data={**data, "room_ids": [a, b]})
    assert r.status_code == 200
    assert "нито една от избраните стаи няма компютри" in r.get_data(as_text=True)
    assert _subject("К") is None
    assert (
        admin.post("/data/subjects/new", data={**data, "room_ids": [a, v]}).status_code
        == 302
    )


def test_viewer_cannot_change_subject_rooms_and_csrf_is_required(make_client, migrated):
    viewer = make_client("viewer")
    assert viewer.get("/data/subjects/new").status_code == 403
    assert viewer.post("/data/subjects/new", data={"name": "Х"}).status_code == 403
    admin = make_client("admin")
    migrated.config["WTF_CSRF_ENABLED"] = True
    assert admin.post("/data/subjects/new", data={"name": "Х"}).status_code == 400
    assert _subject("Х") is None


def test_part_form_hint_and_check_use_subject_rooms(three_rooms):
    admin = three_rooms
    a, b, v = _room_ids()
    admin.post("/data/teachers/new", data={"name": "Иван"})
    admin.post("/data/classes/new", data={"name": "10А", "student_count": "20"})
    admin.post("/data/periods/new", data={"start_time": "08:00", "end_time": "08:40"})
    admin.post(
        "/data/courses/new",
        data={"label": "К", "periods_per_week": "1", "block_sizes": "1"},
    )
    admin.post("/data/subjects/new", data={"name": "А-предмет", "room_ids": [a, b]})
    admin.post(
        "/data/subjects/new",
        data={"name": "Комп", "requires_computers": "y", "room_ids": [v]},
    )
    sid = _subject("А-предмет").id
    base = {
        "groups": [db.session.scalar(select(m.StudentGroup)).id],
        "teachers": [db.session.scalar(select(m.Teacher)).id],
    }
    page = admin.get("/data/courses/1/parts/new").get_data(as_text=True)
    assert "Предметът се води в стаите: А, Б" in page  # first subject is preselected
    # a subject's rooms are ignored by a type that none of them has
    r = admin.post(
        "/data/courses/1/parts/new",
        data={**base, "subject": sid, "needs_room": "y", "room_type": "gym"},
    )
    assert r.status_code == 200 and "Няма стая, която" in r.get_data(as_text=True)
    # the part's own list wins: room В does not belong to the subject, but may be chosen
    r = admin.post(
        "/data/courses/1/parts/new",
        data={**base, "subject": sid, "needs_room": "y", "allowed_room_ids": [v]},
    )
    assert r.status_code == 302
    part = db.session.scalar(select(m.CoursePart))
    assert part.allowed_room_ids == [v]
    page = admin.get(f"/data/courses/1/parts/{part.id}/edit").get_data(as_text=True)
    assert "Предметът се води в стаите" not in page  # it has its own rooms
    # a subject with computers whose rooms are fine: saved
    admin.post(
        "/data/courses/new",
        data={"label": "К2", "periods_per_week": "1", "block_sizes": "1"},
    )
    r = admin.post(
        "/data/courses/2/parts/new",
        data={**base, "subject": _subject("Комп").id},
    )
    assert r.status_code == 302


def test_room_delete_page_mentions_subjects_and_deleting_works(three_rooms):
    admin = three_rooms
    a, b, _v = _room_ids()
    admin.post("/data/subjects/new", data={"name": "Инф", "room_ids": [a, b]})
    page = admin.get(f"/data/rooms/{a}/delete").get_data(as_text=True)
    assert "списъка със стаи на предметите: Инф" in page
    assert "не може да се изтрие" not in page
    assert admin.post(f"/data/rooms/{a}/delete").status_code == 302
    db.session.expire_all()
    assert [r.id for r in _subject("Инф").rooms] == [b]


# --- Excel ---


def test_excel_round_trip_and_old_files(admin, three_rooms):
    a, _b, v = _room_ids()
    three_rooms.post("/data/subjects/new", data={"name": "Инф", "room_ids": [a, v]})
    three_rooms.post("/data/subjects/new", data={"name": "БЕЛ"})
    before = _subject_rooms_snapshot()
    assert before == {"Инф": ["А", "В"], "БЕЛ": []}
    buf = BytesIO()
    build_workbook(True).save(buf)
    wb = load_workbook(BytesIO(buf.getvalue()))
    assert wb[SUBJECTS][1][5].value == "Стаи"
    rows = {r[0]: r[5] for r in wb[SUBJECTS].iter_rows(min_row=2, values_only=True)}
    assert rows["Инф"] == "А; В" and not rows["БЕЛ"]
    assert "Стаи" in HEADERS[SUBJECTS]
    assert upload(admin, buf.getvalue()).status_code == 302
    db.session.expire_all()
    assert _subject_rooms_snapshot() == before
    assert snapshot()  # the whole school is still readable


def _subject_rooms_snapshot():
    return {
        s.name: [r.name for r in s.rooms]
        for s in db.session.scalars(select(m.Subject).order_by(m.Subject.id))
    }


def _minimal_xlsx(subject_row, header_extra=()):
    wb = Workbook()
    wb.remove(wb.active)
    for sheet, headers in HEADERS.items():
        ws = wb.create_sheet(sheet)
        headers = [
            h for h in headers if h != "Стаи" or sheet != SUBJECTS or header_extra
        ]
        ws.append(headers)
        if sheet == "Учебни часове":
            ws.append([1, "08:00", "08:40"])
        if sheet == "Стаи":
            ws.append(["А", "Класна стая", 1, "не", ""])
            ws.append(["Б", "Класна стая", 1, "не", ""])
        if sheet == SUBJECTS:
            ws.append(subject_row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_old_file_without_the_column_still_imports(admin):
    data = _minimal_xlsx(["Инф", "", "", "", "не"])
    assert upload(admin, data).status_code == 302
    assert _subject("Инф").rooms == []


def test_import_reports_unknown_room_and_accepts_known(admin):
    bad = _minimal_xlsx(["Инф", "", "", "", "не", "А; Нямам"], header_extra=("Стаи",))
    r = upload(admin, bad)
    assert r.status_code == 200 and "Непозната стая „Нямам“" in r.get_data(as_text=True)
    assert _subject("Инф") is None
    ok = _minimal_xlsx(["Инф", "", "", "", "не", "А; Б"], header_extra=("Стаи",))
    assert upload(admin, ok).status_code == 302
    assert [r.name for r in _subject("Инф").rooms] == ["А", "Б"]
