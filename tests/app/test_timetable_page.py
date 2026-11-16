from io import BytesIO

from openpyxl import load_workbook
from sqlalchemy import select

from app import models as m
from app.jobs import run_solve
from app.models import db
from tests.app.test_solve_jobs import (
    _add_constraint,
    _all_slots_of_first_teacher,
    _job,
    _load,
)


def _solved(seed="tiny_school.json"):
    _load(seed)
    job_id = _job()
    run_solve(job_id)
    return job_id


def test_both_roles_see_grid_and_report(make_client, migrated):
    _solved()
    for role, button in (("admin", True), ("viewer", False)):
        html = make_client(role).get("/timetable").get_data(as_text=True)
        assert "Понеделник" in html and "Твърди нарушения (0)" in html
        assert ("Оптимално решение" in html) or ("Допустимо решение" in html)
        assert ("Изгради разписание" in html) is button


def test_anonymous_is_sent_to_login(migrated):
    r = migrated.test_client().get("/timetable")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_class_picker_changes_the_grid(make_client, migrated):
    _solved()
    client = make_client("viewer")
    ids = [c.id for c in db.session.scalars(select(m.SchoolClass))]
    pages = {i: client.get(f"/timetable?class={i}").text for i in ids}
    assert len(set(pages.values())) == len(ids)


def test_split_group_lessons_are_both_in_the_cell(make_client, migrated):
    job_id = _solved("demo_school.json")
    # Two parts of a split course share one slot; both must show up for the class.
    from app.routes.solve import _grid

    found = False

    for c in db.session.scalars(select(m.SchoolClass)):
        for entries in _grid(job_id, c.id).values():
            if len(entries) > 1:
                assert all(e["group"] for e in entries)
                found = True
    assert found


def test_teacher_view_shows_only_that_teachers_lessons(make_client, migrated):
    job_id = _solved("demo_school.json")
    from app.routes.solve import _grid

    parts = {p.id: p for p in db.session.scalars(select(m.CoursePart))}
    lessons = db.session.scalars(select(m.Lesson).where(m.Lesson.job_id == job_id))
    lessons = list(lessons)
    joint = split = False
    for t in db.session.scalars(select(m.Teacher)):
        mine = [x for x in lessons if t in parts[x.part_id].teachers]
        expected = sum(x.length for x in mine)
        cells = _grid(job_id, teacher_id=t.id)
        assert sum(len(v) for v in cells.values()) == expected
        # A teacher teaches one thing at a time, so no slot holds two entries.
        assert all(len(v) == 1 for v in cells.values())
        for x in mine:
            groups = parts[x.part_id].groups
            joint |= len({g.class_id for g in groups}) > 1
            split |= not any(g.is_whole for g in groups)
            label = cells[(x.day, x.start_period)][0]["teachers"]
            assert all(
                db.session.get(m.SchoolClass, g.class_id).name in label for g in groups
            )
    assert joint or split  # demo data must exercise a joint or split lesson
    t = db.session.scalars(select(m.Teacher)).first()
    html = make_client("viewer").get(f"/timetable?teacher={t.id}").get_data(True)
    assert f"Седмично разписание на {t.name}" in html


def test_infeasible_shows_plain_message(make_client, migrated):
    _load("impossible_school.json")
    run_solve(_job())
    html = make_client("admin").get("/timetable").get_data(as_text=True)
    assert "Няма решение" in html and "infeasible-explanation" in html
    assert "<table" not in html


def test_infeasible_page_names_the_conflicting_constraints(make_client, migrated):
    _load("tiny_school.json")
    _add_constraint(
        "confirmed", "teacher_unavailable_slots", _all_slots_of_first_teacher()
    )
    _add_constraint("confirmed", "max_lessons_per_day", {"scope": "all", "max": 50})
    culprit = db.session.scalars(select(m.Constraint).order_by(m.Constraint.id)).first()
    culprit.original_text = "Учителят не може да преподава никога"
    db.session.commit()
    run_solve(_job())
    html = make_client("admin").get("/timetable").get_data(as_text=True)
    assert "Ограничения в конфликт" in html
    assert "Учителят не може да преподава никога" in html
    assert "най-много 50" not in html  # the harmless one is not blamed
    assert 'href="/constraints/"' in html


def test_infeasible_without_core_lists_confirmed_constraints_to_disable(
    make_client, migrated
):
    _load("impossible_school.json")  # impossible by itself: the core is empty
    _add_constraint("confirmed", "max_lessons_per_day", {"scope": "all", "max": 50})
    run_solve(_job())
    html = make_client("admin").get("/timetable").get_data(as_text=True)
    assert "Причината не е ясна" in html and "най-много 50" in html


def test_job_card_polls_until_done(make_client, migrated):
    job = m.SolveJob(status="running", time_limit_s=20)
    db.session.add(job)
    db.session.commit()
    client = make_client("viewer")
    r = client.get(f"/jobs/{job.id}/card")
    assert "hx-trigger" in r.text and "HX-Refresh" not in r.headers
    job.status = "failed"
    job.error = "Грешка"
    db.session.commit()
    r = client.get(f"/jobs/{job.id}/card")
    assert "hx-trigger" not in r.text and r.headers["HX-Refresh"] == "true"


def test_excel_export_has_one_valid_sheet_per_class(make_client, migrated):
    _solved("demo_school.json")
    classes = [c.name for c in db.session.scalars(select(m.SchoolClass))]
    for role in ("admin", "viewer"):
        r = make_client(role).get("/timetable.xlsx")
        assert r.status_code == 200 and "spreadsheetml" in r.content_type
        wb = load_workbook(BytesIO(r.data))
        assert wb.sheetnames[:-1] == classes and wb.sheetnames[-1] == "Общ разпис"
        assert all(len(n) <= 31 and not set("[]:*?/\\") & set(n) for n in wb.sheetnames)
        ws = wb[wb.sheetnames[0]]
        assert [c.value for c in ws[1]][:3] == ["Час", "Понеделник", "Вторник"]
        assert ws.cell(2, 1).value.startswith("1\n")
        assert any(c.value for row in ws.iter_rows(min_row=2, min_col=2) for c in row)


def test_excel_export_without_timetable_redirects(make_client, migrated):
    client = make_client("viewer")
    r = client.get("/timetable.xlsx")
    assert r.status_code == 302 and r.headers["Location"].endswith("/timetable")
    assert "Все още няма изградено разписание" in client.get("/timetable").text


def test_excel_export_requires_login(migrated):
    assert migrated.test_client().get("/timetable.xlsx").status_code == 302


def test_resolve_keeps_the_previous_timetable_and_shows_moves(make_client, migrated):
    first = _solved()
    migrated.config["SOLVE_ENQUEUE"] = lambda job_id, timeout: None
    admin = make_client("admin")
    assert "keep-previous" in admin.get("/timetable").get_data(as_text=True)
    admin.post("/solve", data={"keep_previous": "1"})
    second = db.session.scalar(select(m.SolveJob).order_by(m.SolveJob.id.desc()))
    assert second.id != first and second.base_job_id == first
    run_solve(second.id)
    html = admin.get("/timetable").get_data(as_text=True)
    assert f"Няма преместени уроци спрямо изчисление №{first}" in html
    # Without the checkbox the solve is fresh and no comparison is shown.
    db.session.get(m.SolveJob, second.id).status = "failed"
    db.session.commit()
    admin.post("/solve")
    third = db.session.scalar(select(m.SolveJob).order_by(m.SolveJob.id.desc()))
    assert third.base_job_id is None


def test_room_view_shows_only_that_rooms_lessons(make_client, migrated):
    from app.routes.solve import _grid

    job_id = _solved("demo_school.json")
    lessons = list(
        db.session.scalars(select(m.Lesson).where(m.Lesson.job_id == job_id))
    )
    # The demo solve places no rooms; give some lessons one so the view has content.
    room = db.session.scalars(select(m.Room)).first() or m.Room(name="К1", type="x")
    db.session.add(room)
    db.session.flush()
    for x in lessons[::2]:
        x.room_id = room.id
    db.session.commit()
    rooms = {x.room_id for x in lessons if x.room_id}
    for rid in rooms:
        cells = _grid(job_id, room_id=rid)
        assert sum(len(v) for v in cells.values()) == sum(
            x.length for x in lessons if x.room_id == rid
        )
    room = db.session.get(m.Room, next(iter(rooms)))
    html = make_client("viewer").get(f"/timetable?room={room.id}").get_data(True)
    assert f"Седмично разписание на {room.name}" in html
    assert "Покажи по клас" in html and "Печат" in html


def test_overview_has_a_row_per_class_and_is_read_only(make_client, migrated):
    _solved("demo_school.json")
    classes = [c.name for c in db.session.scalars(select(m.SchoolClass))]
    for role in ("admin", "viewer"):
        html = make_client(role).get("/overview").get_data(as_text=True)
        body = html.split("<tbody>")[1]
        assert body.count("<tr>") == len(classes)
        assert all(f">{n}</th>" in body for n in classes)
        assert "subject-" in body and "overview-grid" in html
        assert '<form method="post"' not in body and "data-move-url" not in html
    assert "Общ разпис" in html  # navbar link


def test_overview_without_timetable_redirects_and_needs_login(make_client, migrated):
    assert migrated.test_client().get("/overview").status_code == 302
    r = make_client("viewer").get("/overview")
    assert r.status_code == 302 and r.headers["Location"].endswith("/timetable")


def test_excel_overview_sheet_has_a_row_per_class(make_client, migrated):
    _solved("demo_school.json")
    classes = [c.name for c in db.session.scalars(select(m.SchoolClass))]
    wb = load_workbook(BytesIO(make_client("viewer").get("/timetable.xlsx").data))
    ws = wb["Общ разпис"]
    assert [ws.cell(r, 1).value for r in range(2, ws.max_row + 1)] == classes
    assert ws.cell(1, 2).value.startswith("Понеделник 1")
    assert any(c.value for row in ws.iter_rows(min_row=2, min_col=2) for c in row)


def test_short_subject_and_group_rules():
    from app.routes.solve import short_group, short_subject

    for full, short in {
        "Математика": "Мат.",
        "Философия": "Фил.",
        "Български език и литература": "БЕЛ",
        "Физическо възпитание и спорт": "ФВС",
        "Информационни технологии": "ИТ",
        "Час на класа": "ЧК",
        "Химия и опазване на околната среда": "ХООС",
    }.items():
        assert short_subject(full) == short
    assert (
        short_group("Първа група") == "1 гр." and short_group("Втора група") == "2 гр."
    )
    assert short_group("Момчета") == "Момчета" and short_group("") == ""


def test_overview_uses_short_names_and_keeps_full_name_as_title(make_client, migrated):
    _solved("demo_school.json")
    subject = db.session.scalars(select(m.Subject)).first()
    subject.short_name = None  # the automatic rule must cover a missing short name
    db.session.commit()
    html = make_client("viewer").get("/overview").get_data(as_text=True)
    body = html.split("<tbody>")[1]
    assert f'title="{subject.name}"' in body and f">{subject.name}<" not in body
