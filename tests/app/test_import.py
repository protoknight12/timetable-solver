"""FE-13: Excel/CSV import. The demo seeds exported to the template import back unchanged."""

from copy import deepcopy
from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from app import models as m
from app.cli import load_school
from app.importing import COURSES, HEADERS, PERIODS, ROOMS, TEACHERS, build_workbook
from app.models import db
from tools.make_demo_seeds import SCHOOLS
from tools.seed_to_contract import SEED, load_seed

PAGE = "/data/import/"
BASE = {
    TEACHERS: [["Име"], ["Иван Иванов"]],
    "Предмети": [["Име", "Тип стая по подразбиране"], ["Математика", ""]],
    "Класове": [["Име", "Година на обучение"], ["8А", 8]],
    "Групи": [["Клас", "Име", "Разделяне"], ["8А", "Първа група", "групи"]],
    PERIODS: [
        ["Номер", "Начало", "Край"],
        [1, "08:00", "08:45"],
        [2, "08:55", "09:40"],
    ],
    COURSES: [
        HEADERS[COURSES],
        [
            1,
            "",
            2,
            "1, 1",
            "Математика",
            "8А — Първа група",
            "Иван Иванов",
            "не",
            "",
            "",
        ],
    ],
}


def xlsx(sheets) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(client, data: bytes, name="d.xlsx", **form):
    form = {"confirm": "y", **form}
    return client.post(
        PAGE,
        data={"file": (BytesIO(data), name), **form},
        content_type="multipart/form-data",
    )


def snapshot():
    """The school's data without ids, so two databases can be compared."""
    s = db.session
    ref = lambda g: g.school_class.name + ("" if g.is_whole else f" — {g.name}")
    return {
        "teachers": sorted(t.name for t in s.scalars(select(m.Teacher))),
        "subjects": sorted(
            (x.name, x.default_room_type, x.color, x.short_name)
            for x in s.scalars(select(m.Subject))
        ),
        "rooms": sorted(
            (r.name, r.type, r.concurrent_capacity) for r in s.scalars(select(m.Room))
        ),
        "classes": sorted((c.name, c.grade) for c in s.scalars(select(m.SchoolClass))),
        "groups": sorted(
            (ref(g), g.is_whole, g.split) for g in s.scalars(select(m.StudentGroup))
        ),
        "periods": sorted(
            (p.number, p.start_time, p.end_time) for p in s.scalars(select(m.Period))
        ),
        "courses": sorted(
            (
                c.label,
                c.periods_per_week,
                tuple(c.block_sizes),
                sorted(
                    (
                        p.subject.name,
                        sorted(ref(g) for g in p.groups),
                        sorted(t.name for t in p.teachers),
                        p.needs_room,
                        p.room_type,
                        sorted(
                            db.session.get(m.Room, i).name
                            for i in p.allowed_room_ids or []
                        ),
                    )
                    for p in c.parts
                ),
            )
            for c in s.scalars(select(m.Course))
        ),
    }


def count(model):
    return db.session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize("name", list(SCHOOLS))
def test_demo_seed_round_trips_through_the_template(make_client, name):
    load_school(load_seed(SEED.parent / name))
    db.session.commit()
    before = snapshot()
    buf = BytesIO()
    build_workbook(True).save(buf)
    r = upload(make_client("admin"), buf.getvalue())
    assert r.status_code == 302, r.get_data(as_text=True)
    assert snapshot() == before


def test_round_trip_keeps_room_requirements(make_client):
    load_school(load_seed(SEED.parent / "tiny_school.json"))
    part = db.session.scalars(select(m.CoursePart)).first()
    room = db.session.scalars(select(m.Room)).first()
    part.needs_room, part.room_type, part.allowed_room_ids = True, room.type, [room.id]
    db.session.commit()
    before = snapshot()
    buf = BytesIO()
    build_workbook(True).save(buf)
    assert upload(make_client("admin"), buf.getvalue()).status_code == 302
    assert snapshot() == before
    assert any(c[3][0][3] for c in before["courses"])


def test_template_and_export_download(make_client):
    admin = make_client("admin")
    for url in ("template.xlsx", "export.xlsx"):
        r = admin.get(PAGE + url)
        assert r.status_code == 200 and r.data[:2] == b"PK"
    # An empty template has the Bulgarian headers and no data rows.
    from openpyxl import load_workbook

    wb = load_workbook(BytesIO(admin.get(PAGE + "template.xlsx").data))
    assert wb["Учители"]["A1"].value == "Име" and wb["Учители"].max_row == 1
    assert {"Учители", "Предмети", "Стаи", "Класове", "Учебен план"} <= set(
        wb.sheetnames
    )


def test_import_into_an_empty_database(make_client):
    r = upload(make_client("admin"), xlsx(BASE))
    assert r.status_code == 302
    snap = snapshot()
    assert snap["teachers"] == ["Иван Иванов"]
    assert ("8А — Първа група", False, "групи") in snap["groups"]
    assert snap["courses"][0][3][0][1] == ["8А — Първа група"]


def test_replace_needs_the_confirm_checkbox(make_client):
    load_school(load_seed(SEED.parent / "tiny_school.json"))
    db.session.commit()
    before = snapshot()
    r = upload(make_client("admin"), xlsx(BASE), confirm="")
    assert "Потвърдете замяната" in r.get_data(as_text=True)
    assert snapshot() == before


def _expect_errors(client, sheets, *messages):
    load_school(load_seed(SEED.parent / "tiny_school.json"))
    db.session.commit()
    before = snapshot()
    r = upload(client, xlsx(sheets))
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Нищо не е запазено" in html
    for message in messages:
        assert message in html, message
    assert snapshot() == before  # all or nothing, the old data is still there


def test_row_errors_name_sheet_and_row_and_save_nothing(make_client):
    sheets = deepcopy(BASE)
    sheets[TEACHERS] += [["Иван Иванов"], [None, "x"], ["Мария"]]  # duplicate in row 3
    sheets[TEACHERS].append(["Петър"])
    sheets["Предмети"] += [["Математика", ""]]
    sheets["Класове"] += [["9Б", 99]]
    _expect_errors(
        make_client("admin"),
        sheets,
        "Лист „Учители“, ред 3: Дублиран ред",
        "Лист „Предмети“, ред 3: Дублиран ред",
        "Лист „Класове“, ред 3: Година на обучение",
    )


def test_missing_field_and_bad_number(make_client):
    sheets = deepcopy(BASE)
    sheets[TEACHERS] += [[" "], ["Х"]]  # blank row skipped
    sheets["Стаи"] = [HEADERS[ROOMS], ["", "lab", 1], ["Стая", "lab", "много"]]
    _expect_errors(
        make_client("admin"),
        sheets,
        "Лист „Стаи“, ред 2: Име: Полето е задължително.",
        "Лист „Стаи“, ред 3: Едновременни часове: Въведете цяло число.",
    )


def test_curriculum_errors(make_client):
    ok = BASE[COURSES][1]
    bad_rows = [
        [2, "", 2, "1, 1", "Математика", "8А", "Няма такъв", "не", "", ""],
        [3, "", 2, "1, 1", "Химия", "9Ж", "Иван Иванов", "не", "", ""],
        [4, "", 2, "3", "Математика", "8А", "Иван Иванов", "не", "", ""],
        [5, "", 2, "1, 1", "Математика", "8А", "Иван Иванов", "може би", "", ""],
        [6, "", 2, "1, 1", "Математика", "8А", "", "не", "", ""],
        [1, "", 3, "1, 2", "Математика", "8А", "Иван Иванов", "не", "", ""],
    ]
    sheets = deepcopy(BASE)
    sheets[COURSES] = [HEADERS[COURSES], ok, *bad_rows]
    _expect_errors(
        make_client("admin"),
        sheets,
        "ред 3: Учители: Непознат учител „Няма такъв“",
        "ред 4: Предмет: Непознат предмет „Химия“",
        "ред 4: Групи: Непознат клас или група „9Ж“",
        "ред 5: Блокове: Сборът на блоковете (3)",
        "ред 6: Изисква стая: Въведете „да“ или „не“",
        "ред 7: Учители: Изберете поне един учител",
        "ред 8: Редовете с един и същ код",
    )


def test_missing_column_and_empty_file(make_client):
    admin = make_client("admin")
    load_school(load_seed(SEED.parent / "tiny_school.json"))
    db.session.commit()
    before = snapshot()
    html = upload(admin, xlsx({TEACHERS: [["Фамилия"], ["Иванов"]]})).get_data(
        as_text=True
    )
    assert "липсва колона „Име“" in html
    buf = BytesIO()
    build_workbook(False).save(buf)  # the empty template must not wipe the data
    assert "няма данни за импорт" in upload(admin, buf.getvalue()).get_data(
        as_text=True
    )
    assert "нито един от очакваните листове" in upload(
        admin, xlsx({"Друг": [["a"]]})
    ).get_data(as_text=True)
    assert snapshot() == before


def test_corrupt_and_unwanted_files_give_bulgarian_errors_not_500(make_client):
    admin = make_client("admin")
    for data in (b"not a zip at all", b"PK\x03\x04garbage", b""):
        r = upload(admin, data)
        assert r.status_code == 200
        assert any(t in r.get_data(as_text=True) for t in ("повреден", "Изберете файл"))
    r = upload(admin, b"x", name="d.txt")
    assert "Позволени са само файлове .xlsx и .csv" in r.get_data(as_text=True)
    r = upload(admin, b"x" * (2 * 1024 * 1024 + 1))
    assert r.status_code == 413 and "твърде голям" in r.get_data(as_text=True)
    assert count(m.Teacher) == 0


def test_csv_adds_one_sheet_to_existing_data(make_client):
    load_school(load_seed(SEED.parent / "tiny_school.json"))
    db.session.commit()
    n, admin = count(m.Teacher), make_client("admin")
    existing = db.session.scalars(select(m.Teacher.name)).first()
    csv = "Име\r\nНов Учител\r\nДруг Учител\r\n".encode("cp1251")  # Excel (bg) encoding
    r = upload(admin, csv, name="u.csv", sheet="Учители", confirm="")
    assert r.status_code == 302 and count(m.Teacher) == n + 2
    assert count(m.Course) > 0  # nothing else was touched
    # Semicolons; an existing name is a duplicate and nothing is added.
    csv = f"Име;\nПети;\n{existing};\n".encode()
    r = upload(admin, csv, name="u.csv", sheet="Учители", confirm="")
    assert "ред 3: Дублиран ред" in r.get_data(as_text=True)
    assert count(m.Teacher) == n + 2
    r = upload(admin, csv, name="u.csv", sheet="")
    assert "Изберете към кой лист" in r.get_data(as_text=True)


def test_import_page_is_admin_only(make_client, migrated):
    viewer = make_client("viewer")
    for path in ("", "template.xlsx", "export.xlsx"):
        assert viewer.get(PAGE + path).status_code == 403
        r = migrated.test_client().get(PAGE + path)
        assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert viewer.post(PAGE).status_code == 403
    assert "Импорт на данни" in make_client("admin").get(PAGE).get_data(as_text=True)


def test_import_requires_csrf(migrated):
    from app.auth import create_user

    create_user("a", "test-password-1", "admin")
    client = migrated.test_client()
    client.post("/login", data={"username": "a", "password": "test-password-1"})
    migrated.config["WTF_CSRF_ENABLED"] = True
    r = client.post(PAGE, data={"file": (BytesIO(b"x"), "d.xlsx"), "confirm": "y"})
    assert r.status_code == 400
