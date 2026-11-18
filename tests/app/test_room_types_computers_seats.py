"""BE-20: fixed room types, computers, seats and student counts."""

import json
from io import BytesIO

import pytest
import sqlalchemy as sa
from flask_migrate import upgrade
from openpyxl import load_workbook
from sqlalchemy import select

from app import models as m
from app.cli import load_school
from app.importing import COURSES, HEADERS, ROOMS, SUBJECTS, build_workbook
from app.models import db
from app.room_types import (
    ROOM_TYPES,
    normalize_room_type,
    room_type_choices,
    room_type_label,
)
from app.solver_input import build_solver_input
from tests.app.test_import import BASE, snapshot, upload, xlsx
from tools.seed_to_contract import SEED, load_seed


@pytest.fixture
def admin(make_client):
    return make_client("admin")


def _room(name):
    return db.session.scalar(select(m.Room).where(m.Room.name == name))


# --- the list itself ---


def test_codes_labels_and_normalising():
    assert [c for c, _ in ROOM_TYPES] == [
        "classroom",
        "lab",
        "lecture",
        "gym",
        "workshop",
        "other",
    ]
    assert room_type_label("gym") == "Физкултурен салон"
    assert room_type_label(None) == "—" and room_type_label("old") == "old"
    for text, code in [
        ("lab", "lab"),
        ("Лаборатория / STEM кабинет", "lab"),
        ("  класна стая ", "classroom"),
        ("стая", "classroom"),
        ("regular", "classroom"),
        ("ДРУГО", "other"),
    ]:
        assert normalize_room_type(text) == code
    assert normalize_room_type("Нещо") is None and normalize_room_type("") is None
    assert room_type_choices("Без изискване")[0] == ("", "Без изискване")
    assert room_type_choices() == list(ROOM_TYPES)


# --- room pages ---


def test_room_form_is_a_dropdown_with_bulgarian_labels(admin):
    html = admin.get("/data/rooms/new").get_data(as_text=True)
    assert "<select" in html and 'name="type"' in html
    for code, label in ROOM_TYPES:
        assert f'<option value="{code}">{label}</option>' in html
    assert 'name="has_computers"' in html and "Има компютри" in html
    assert 'name="seats"' in html and "Места" in html
    assert "datalist" not in html


def test_room_saved_listed_with_label_and_new_fields(admin):
    r = admin.post(
        "/data/rooms/new",
        data={
            "name": "Каб. 1",
            "type": "lab",
            "concurrent_capacity": "1",
            "has_computers": "y",
            "seats": "28",
        },
    )
    assert r.status_code == 302
    room = _room("Каб. 1")
    assert (room.type, room.has_computers, room.seats) == ("lab", True, 28)
    page = admin.get("/data/rooms/").get_data(as_text=True)
    assert "Лаборатория / STEM кабинет" in page and ">lab<" not in page
    assert "28" in page and "да" in page


def test_room_defaults_and_validation(admin):
    base = {"name": "A", "concurrent_capacity": "1"}
    assert (
        admin.post("/data/rooms/new", data={**base, "type": "gym"}).status_code == 302
    )
    room = _room("A")
    assert (room.has_computers, room.seats) == (False, None)
    for bad in ({"type": "Lab"}, {"type": ""}, {"type": "classroom", "seats": "0"}):
        r = admin.post("/data/rooms/new", data={**base, "name": "B", **bad})
        assert r.status_code == 200, bad
    assert _room("B") is None
    # edit: the stored value is preselected
    html = admin.get(f"/data/rooms/{room.id}/edit").get_data(as_text=True)
    assert '<option selected value="gym">' in html


# --- subject pages ---


def test_subject_dropdown_and_computers(admin):
    html = admin.get("/data/subjects/new").get_data(as_text=True)
    assert 'value="">Без изискване</option>' in html
    assert "Изисква компютри" in html
    r = admin.post(
        "/data/subjects/new",
        data={
            "name": "Информатика",
            "default_room_type": "lab",
            "requires_computers": "y",
        },
    )
    assert r.status_code == 302
    s = db.session.scalar(select(m.Subject))
    assert (s.default_room_type, s.requires_computers) == ("lab", True)
    admin.post(
        f"/data/subjects/{s.id}/edit",
        data={"name": "Информатика", "default_room_type": ""},
    )
    db.session.refresh(s)
    assert (s.default_room_type, s.requires_computers) == (None, False)
    r = admin.post(
        "/data/subjects/new", data={"name": "Х", "default_room_type": "стая"}
    )
    assert r.status_code == 200 and "Изберете тип стая" in r.get_data(as_text=True)
    assert "Лаборатория" not in admin.get("/data/subjects/").get_data(as_text=True)


# --- classes and groups ---


def test_student_counts_on_class_and_group_pages(admin):
    r = admin.post("/data/classes/new", data={"name": "10А", "student_count": "26"})
    assert r.status_code == 302
    k = db.session.scalar(select(m.SchoolClass))
    assert k.student_count == 26
    bad = admin.post("/data/classes/new", data={"name": "10Б", "student_count": "0"})
    assert bad.status_code == 200 and "поне 1" in bad.get_data(as_text=True)
    r = admin.post(
        f"/data/classes/{k.id}/groups/new",
        data={"name": "Група 1", "split": "групи", "student_count": "13"},
    )
    assert r.status_code == 302
    g = db.session.scalar(select(m.StudentGroup).where(~m.StudentGroup.is_whole))
    assert g.student_count == 13
    bad = admin.post(
        f"/data/classes/{k.id}/groups/new",
        data={"name": "Група 2", "split": "групи", "student_count": "-4"},
    )
    assert bad.status_code == 200
    page = admin.get(f"/data/classes/{k.id}/edit").get_data(as_text=True)
    assert "Ученици" in page and ">13<" in page


# --- parts: dropdown and the solver input ---


@pytest.fixture
def school(admin):
    admin.post("/data/teachers/new", data={"name": "Иван"})
    admin.post(
        "/data/subjects/new",
        data={"name": "Информатика", "requires_computers": "y"},
    )
    admin.post("/data/subjects/new", data={"name": "БЕЛ"})
    admin.post(
        "/data/rooms/new",
        data={"name": "Стая", "type": "classroom", "concurrent_capacity": "1"},
    )
    admin.post("/data/classes/new", data={"name": "10А", "student_count": "26"})
    admin.post("/data/periods/new", data={"start_time": "08:00", "end_time": "08:40"})
    admin.post(
        "/data/courses/new",
        data={"label": "К", "periods_per_week": "1", "block_sizes": "1"},
    )
    return admin


def _part(admin, subject, **over):
    subjects = {s.name: s.id for s in db.session.scalars(select(m.Subject))}
    data = {
        "subject": subjects[subject],
        "groups": [db.session.scalar(select(m.StudentGroup)).id],
        "teachers": [db.session.scalar(select(m.Teacher)).id],
        **over,
    }
    return admin.post("/data/courses/1/parts/new", data=data)


def test_part_room_type_is_a_dropdown(school):
    html = school.get("/data/courses/1/parts/new").get_data(as_text=True)
    assert 'value="">Без изискване</option>' in html
    assert '<option value="gym">Физкултурен салон</option>' in html
    assert "datalist" not in html
    r = _part(school, "БЕЛ", needs_room="y", room_type="Lab")
    assert r.status_code == 200 and "Изберете тип стая" in r.get_data(as_text=True)
    assert _part(school, "БЕЛ", needs_room="y", room_type="").status_code == 302
    assert db.session.scalar(select(m.CoursePart)).room_type is None


def test_computer_subject_needs_a_computer_room_in_the_form(school):
    # the only room has no computers
    r = _part(school, "Информатика")
    assert r.status_code == 200 and "Няма стая, която" in r.get_data(as_text=True)
    room = _room("Стая")
    school.post(
        f"/data/rooms/{room.id}/edit",
        data={
            "name": "Стая",
            "type": "classroom",
            "concurrent_capacity": "1",
            "has_computers": "y",
        },
    )
    assert _part(school, "Информатика").status_code == 302
    page = school.get("/data/courses/1/edit").get_data(as_text=True)
    assert "Стая с компютри" in page


def test_solver_input_carries_the_new_fields_and_implies_a_room(school):
    room = _room("Стая")
    school.post(
        f"/data/rooms/{room.id}/edit",
        data={
            "name": "Стая",
            "type": "classroom",
            "concurrent_capacity": "1",
            "has_computers": "y",
            "seats": "30",
        },
    )
    _part(school, "Информатика")
    inp = build_solver_input(db.session)
    assert inp.problems() == []
    (r,), (s1, s2), (k,) = inp.rooms, inp.subjects, inp.classes
    assert (r.has_computers, r.seats) == (True, 30)
    assert (s1.requires_computers, s2.requires_computers) == (True, False)
    assert k.student_count == 26
    p = inp.courses[0].parts[0]
    assert p.needs_room is True  # implied by the subject; the part said no
    assert inp.to_dict()["contract_version"] == 4


# --- Excel template ---


def test_import_accepts_code_and_label_and_yes_no(admin):
    sheets = {
        **BASE,
        SUBJECTS: [
            [*HEADERS[SUBJECTS]],
            ["Математика", "Лаборатория / STEM кабинет", "", "", "да"],
            ["БЕЛ", "", "", "", "не"],
        ],
        ROOMS: [
            [*HEADERS[ROOMS]],
            ["Каб. 1", "lab", 1, "да", 24],
            ["Салон", "Физкултурен салон", 3, "", ""],
            ["Стая 1", "класна стая", 1, "не", ""],
        ],
        "Класове": [
            ["Име", "Година на обучение", "Класен ръководител", "Брой ученици"],
            ["8А", 8, "", 27],
        ],
        "Групи": [
            ["Клас", "Име", "Разделяне", "Брой ученици"],
            ["8А", "Първа група", "групи", 13],
        ],
    }
    r = upload(admin, xlsx(sheets))
    assert r.status_code == 302, r.get_data(as_text=True)
    info = db.session.scalar(select(m.Subject).where(m.Subject.name == "Математика"))
    assert (info.default_room_type, info.requires_computers) == ("lab", True)
    assert [
        (x.name, x.type, x.has_computers, x.seats)
        for x in db.session.scalars(select(m.Room).order_by(m.Room.id))
    ] == [
        ("Каб. 1", "lab", True, 24),
        ("Салон", "gym", False, None),
        ("Стая 1", "classroom", False, None),
    ]
    assert db.session.scalar(select(m.SchoolClass)).student_count == 27
    assert (
        db.session.scalar(
            select(m.StudentGroup).where(~m.StudentGroup.is_whole)
        ).student_count
        == 13
    )


def test_import_reports_unknown_type_seats_and_bad_yes_no(admin):
    def room_row(*cells):
        return {**BASE, ROOMS: [[*HEADERS[ROOMS]], ["Каб. 1", *cells]]}

    r = upload(admin, xlsx(room_row("lab", 1, "може би", 5)))
    assert r.status_code == 200
    assert "Въведете „да“ или „не“" in r.get_data(as_text=True)
    r = upload(admin, xlsx(room_row("Кабинет", 1, "да", 0)))
    text = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Изберете тип стая" in text and "поне 1" in text
    assert db.session.scalar(select(m.Room)) is None


def test_old_files_without_the_new_columns_still_import(admin):
    sheets = {
        **BASE,
        ROOMS: [["Име", "Тип", "Едновременни часове"], ["Каб. 1", "lab", 1]],
    }
    assert upload(admin, xlsx(sheets)).status_code == 302
    room = _room("Каб. 1")
    assert (room.has_computers, room.seats) == (False, None)


def test_export_writes_labels_and_round_trips_the_new_fields(admin):
    load_school(load_seed(SEED))
    for room in db.session.scalars(select(m.Room)).all()[:2]:
        room.has_computers, room.seats = True, 24
    db.session.scalars(select(m.Subject)).first().requires_computers = True
    k = db.session.scalars(select(m.SchoolClass)).first()
    k.student_count = 25
    g = next(x for x in db.session.scalars(select(m.StudentGroup)) if not x.is_whole)
    g.student_count = 12
    part = db.session.scalars(select(m.CoursePart)).first()
    part.needs_room, part.room_type, part.allowed_room_ids = True, "lab", None
    db.session.commit()
    before = snapshot()
    buf = BytesIO()
    build_workbook(True).save(buf)
    wb = load_workbook(BytesIO(buf.getvalue()))
    types = {row[1] for row in wb[ROOMS].iter_rows(min_row=2, values_only=True)}
    assert "Класна стая" in types and "classroom" not in types
    assert {row[8] for row in wb[COURSES].iter_rows(min_row=2, values_only=True)} >= {
        "Лаборатория / STEM кабинет"
    }
    assert upload(admin, buf.getvalue()).status_code == 302
    assert snapshot() == before
    db.session.expire_all()
    assert sum(r.has_computers for r in db.session.scalars(select(m.Room))) == 2
    assert sum(s.requires_computers for s in db.session.scalars(select(m.Subject))) == 1
    assert db.session.scalars(select(m.SchoolClass)).first().student_count == 25
    assert (
        sum(x.student_count == 12 for x in db.session.scalars(select(m.StudentGroup)))
        == 1
    )


# --- the data migration ---


def test_migration_maps_old_room_types(app):
    upgrade(directory="migrations", revision="a40d1c2d3e4f")
    with db.engine.begin() as c:
        for i, t in enumerate(
            [
                "стая",
                "regular",
                "lab",
                "gym",
                "Лаборатория",
                "Спортна зала",
                "Нещо",
                "Каб",
                "",
            ],
            1,
        ):
            c.execute(
                sa.text(
                    "INSERT INTO rooms (id, name, type, concurrent_capacity) VALUES (:i, :n, :t, 1)"
                ),
                {"i": i, "n": f"R{i}", "t": t},
            )
        c.execute(
            sa.text(
                "INSERT INTO subjects (id, name, default_room_type) VALUES (1, 'S', 'стая'), (2, 'T', NULL)"
            )
        )
        c.execute(
            sa.text(
                "INSERT INTO courses (id, periods_per_week, block_sizes, label) VALUES (1, 1, '[1]', '')"
            )
        )
        c.execute(
            sa.text(
                "INSERT INTO course_parts (id, course_id, subject_id, needs_room, room_type) "
                "VALUES (1, 1, 1, TRUE, 'regular'), (2, 1, 2, TRUE, NULL)"
            )
        )
        c.execute(
            sa.text(
                "INSERT INTO constraints (id, type, params, hard, status, source) "
                "VALUES (1, 'room_type_required', :p, TRUE, 'confirmed', 'manual')"
            ),
            {"p": '{"subject_id": 1, "room_type": "regular"}'},
        )
    upgrade(directory="migrations")
    with db.engine.connect() as c:
        rooms = [r[0] for r in c.execute(sa.text("SELECT type FROM rooms ORDER BY id"))]
        assert rooms == [
            "classroom",
            "classroom",
            "lab",
            "gym",
            "lab",
            "gym",
            "other",
            "other",
            "other",
        ]
        assert c.execute(
            sa.text("SELECT default_room_type FROM subjects ORDER BY id")
        ).all() == [
            ("classroom",),
            (None,),
        ]
        assert c.execute(
            sa.text("SELECT room_type FROM course_parts ORDER BY id")
        ).all() == [
            ("classroom",),
            (None,),
        ]
        params = c.execute(sa.text("SELECT params FROM constraints")).scalar_one()
        if isinstance(params, str):  # SQLite returns JSON text, PostgreSQL a dict
            params = json.loads(params)
        assert params["room_type"] == "classroom"
        # the new columns have their defaults
        assert c.execute(
            sa.text("SELECT has_computers, seats FROM rooms WHERE id = 1")
        ).one() == (0, None)
        assert (
            c.execute(
                sa.text("SELECT requires_computers FROM subjects WHERE id = 1")
            ).scalar_one()
            == 0
        )
