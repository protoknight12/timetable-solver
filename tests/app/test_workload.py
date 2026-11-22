"""FE-17: workload report per teacher."""

from io import BytesIO

from openpyxl import load_workbook

from app import models as m
from app.models import db
from app.routes.workload import compute


def _tiny(published=True):
    """Ivan: Mon periods 1 and 3 (one gap), Tue periods 2-3. Maria teaches nothing."""
    ivan, maria = m.Teacher(name="Иван Иванов"), m.Teacher(name="Мария Георгиева")
    math, art = m.Subject(name="Математика"), m.Subject(name="Изобразително")
    cls = m.SchoolClass(name="5А", grade=5)
    cls.groups.append(m.StudentGroup(name="", is_whole=True))
    db.session.add_all([ivan, maria, math, art, cls])
    db.session.flush()
    parts = []
    for ppw, subj in ((3, math), (1, art)):
        course = m.Course(periods_per_week=ppw, block_sizes=[1] * ppw)
        part = m.CoursePart(subject=subj, teachers=[ivan], groups=cls.groups)
        course.parts.append(part)
        db.session.add(course)
        parts.append(part)
    db.session.flush()
    job = m.SolveJob(
        status="finished",
        solver_status="OPTIMAL",
        published_at=db.func.now() if published else None,
    )
    db.session.add(job)
    db.session.flush()
    for part, day, start, length in (
        (parts[0], 1, 1, 1),
        (parts[1], 1, 3, 1),
        (parts[0], 2, 2, 2),
    ):
        db.session.add(
            m.Lesson(
                job_id=job.id,
                course_id=part.course_id,
                part_id=part.id,
                day=day,
                start_period=start,
                length=length,
            )
        )
    db.session.commit()
    return job, ivan, maria


def test_numbers_for_a_known_timetable(migrated):
    job, ivan, maria = _tiny()
    rows = compute(job.id)
    r = rows[ivan.id]
    assert (r["periods"], r["courses"], r["classes"], r["days"]) == (4, 2, 1, 2)
    assert (r["gaps"], r["worst_gaps"], r["first"], r["last"]) == (1, 1, 1, 3)
    assert r["subjects"] == {"Изобразително": 1, "Математика": 3}
    assert r["per_day"][2] == {"periods": 2, "first": 2, "last": 3, "gaps": 0}
    assert rows[maria.id]["periods"] == 0 and rows[maria.id]["first"] is None


def test_over_limit_badge_needs_a_confirmed_constraint(make_client, migrated):
    _, ivan, _ = _tiny()
    client = make_client("admin")
    for status in ("proposed", "confirmed"):
        db.session.add(
            m.Constraint(
                type="teacher_max_lessons_per_day",
                params={"teacher_id": ivan.id, "max": 1},
                status=status,
            )
        )
        db.session.commit()
        html = client.get("/workload").text
        assert ("Над лимита: 2 от 1 часа" in html) is (status == "confirmed")


def test_admin_sees_all_sorted_with_totals_and_version(make_client, migrated):
    _tiny()
    client = make_client("admin")
    html = client.get("/workload?sort=periods&dir=desc").text
    assert html.index("Иван Иванов") < html.index("Мария Георгиева")
    assert "Общо" in html and "от публикуваната версия" in html
    assert 'aria-sort="descending"' in html
    assert client.get("/workload?sort=bogus").status_code == 200


def test_draft_is_named_for_admin(make_client, migrated):
    _tiny(published=False)
    assert "от чернова" in make_client("admin").get("/workload").text


def test_linked_teacher_sees_only_self(make_client, migrated):
    _, ivan, maria = _tiny()
    client = make_client("viewer")
    user = db.session.scalar(db.select(m.User))
    user.teacher_id = ivan.id
    db.session.commit()
    html = client.get("/workload").text
    assert "Иван Иванов" in html and "Мария Георгиева" not in html
    assert client.get(f"/workload/{ivan.id}").status_code == 200
    assert client.get(f"/workload/{maria.id}").status_code == 403
    sheet = load_workbook(BytesIO(client.get("/workload.xlsx").data)).active
    assert [c.value for c in sheet["A"]] == ["Учител", "Иван Иванов"]


def test_unlinked_viewer_gets_a_message_and_no_data(make_client, migrated):
    _tiny()
    client = make_client("viewer")
    r = client.get("/workload")
    assert "не е свързан с учител" in r.text and "Иван Иванов" not in r.text
    assert client.get("/workload/1").status_code == 403
    assert client.get("/workload.xlsx").status_code == 403


def test_anonymous_goes_to_login(migrated):
    for url in ("/workload", "/workload/1", "/workload.xlsx"):
        assert migrated.test_client().get(url).status_code == 302


def test_admin_export_has_all_rows_and_totals(make_client, migrated):
    _tiny()
    r = make_client("admin").get("/workload.xlsx")
    sheet = load_workbook(BytesIO(r.data)).active
    names = [c.value for c in sheet["A"]]
    assert names == ["Учител", "Иван Иванов", "Мария Георгиева", "Общо"]
    assert sheet["B4"].value == 4 and sheet["G4"].value == 1


def test_detail_shows_per_day_and_subject(make_client, migrated):
    _, ivan, _ = _tiny()
    html = make_client("admin").get(f"/workload/{ivan.id}").text
    assert "Математика" in html and "Понеделник" in html


def test_norm_is_compared_with_the_timetable(migrated):
    """Ivan teaches 4 periods: over a norm of 3, under 5, fine at 4, no note without a norm."""
    _, ivan, _ = _tiny()
    states = {}
    for norm in (3, 5, 4, None):
        ivan.weekly_norm = norm
        db.session.commit()
        job_id = db.session.scalar(db.select(m.SolveJob.id))
        r = compute(job_id)[ivan.id]
        states[norm] = (r["norm"], r["norm_state"])
    assert states == {
        3: (3, "over"),
        5: (5, "under"),
        4: (4, "ok"),
        None: (None, None),
    }


def test_norm_shows_on_page_and_in_export(make_client, migrated):
    _, ivan, maria = _tiny()
    ivan.weekly_norm, maria.weekly_norm = 3, 18
    db.session.commit()
    client = make_client("admin")
    html = client.get("/workload").text
    assert "Над нормата: 4 от 3 часа" in html
    assert "Под нормата: 0 от 18 часа" in html
    sheet = load_workbook(BytesIO(client.get("/workload.xlsx").data)).active
    assert [c.value for c in sheet[1]][:3] == ["Учител", "Часове", "Норма"]
    assert [c.value for c in sheet["C"]][1:3] == [3, 18]
    assert "над нормата (4 от 3 часа)" in sheet["K2"].value
    assert sheet["B4"].value == 4 and sheet["G4"].value == 1  # totals row
