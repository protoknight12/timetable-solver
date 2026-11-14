"""FE-05a: data entry pages for teachers, subjects, rooms, classes, groups and periods."""

import pytest
from sqlalchemy import inspect, select

from app.models import (
    Course,
    CoursePart,
    Lesson,
    Period,
    Room,
    SchoolClass,
    SolveJob,
    StudentGroup,
    Subject,
    Teacher,
    db,
)

# key -> (model, valid create data, valid edit data, invalid data, edited attribute)
CASES = {
    "teachers": (
        Teacher,
        {"name": "Иван Петров"},
        {"name": "Мария Иванова"},
        {"name": "  "},
        "name",
    ),
    "subjects": (
        Subject,
        {"name": "Химия", "default_room_type": "lab"},
        {"name": "Физика", "default_room_type": ""},
        {"name": ""},
        "name",
    ),
    "rooms": (
        Room,
        {"name": "Кабинет 1", "type": "lab", "concurrent_capacity": "1"},
        {"name": "Салон", "type": "gym", "concurrent_capacity": "2"},
        {"name": "Кабинет 1", "type": "lab", "concurrent_capacity": "0"},
        "name",
    ),
    "classes": (
        SchoolClass,
        {"name": "10А", "grade": "10"},
        {"name": "11Б", "grade": ""},
        {"name": "10А", "grade": "13"},
        "name",
    ),
    "periods": (
        Period,
        {"start_time": "08:00", "end_time": "08:40"},
        {"start_time": "08:00", "end_time": "08:45"},
        {"start_time": "09:00", "end_time": "08:00"},
        "end_time",
    ),
}


def _rows(model):
    # Order by primary key: PostgreSQL has no default row order (an UPDATE can
    # move a row to the end), SQLite happens to return insertion order.
    db.session.expire_all()
    pk = inspect(model).primary_key
    return db.session.scalars(select(model).order_by(*pk)).all()


def _pk(row):
    return row.number if isinstance(row, Period) else row.id


@pytest.fixture
def admin(make_client):
    return make_client("admin")


@pytest.mark.parametrize("key", CASES)
def test_admin_creates_edits_and_deletes(admin, key):
    model, create, edit, _, attr = CASES[key]
    r = admin.post(f"/data/{key}/new", data=create, follow_redirects=True)
    assert r.status_code == 200 and "добавен" in r.get_data(as_text=True)
    (row,) = _rows(model)
    pk = _pk(row)

    r = admin.post(f"/data/{key}/{pk}/edit", data=edit, follow_redirects=True)
    assert "запазен" in r.get_data(as_text=True)
    (row,) = _rows(model)
    assert str(getattr(row, attr)).startswith(edit[attr])

    assert admin.get(f"/data/{key}/{pk}/delete").status_code == 200  # confirmation
    r = admin.post(f"/data/{key}/{pk}/delete", follow_redirects=True)
    assert "изтрит" in r.get_data(as_text=True)
    assert _rows(model) == []


@pytest.mark.parametrize("key", CASES)
def test_invalid_input_shows_error_and_saves_nothing(admin, key):
    model, create, _, bad, _ = CASES[key]
    r = admin.post(f"/data/{key}/new", data=bad)
    assert r.status_code == 200 and "invalid-feedback" in r.get_data(as_text=True)
    assert _rows(model) == []
    admin.post(f"/data/{key}/new", data=create)
    (row,) = _rows(model)
    r = admin.post(f"/data/{key}/{_pk(row)}/edit", data=bad)
    assert "invalid-feedback" in r.get_data(as_text=True)
    (after,) = _rows(model)
    assert after is row and _pk(after) == _pk(row)


def test_values_are_trimmed(admin):
    admin.post("/data/teachers/new", data={"name": "  Георги  "})
    assert _rows(Teacher)[0].name == "Георги"


def _numbers():
    return sorted(p.number for p in _rows(Period))


def _add_period(admin, start="08:00", end="08:40", **extra):
    return admin.post(
        "/data/periods/new", data={"start_time": start, "end_time": end, **extra}
    )


def test_periods_get_consecutive_numbers_and_only_the_last_can_go(admin):
    for _ in range(3):
        _add_period(admin, number="9")  # a posted number is ignored
    assert _numbers() == [1, 2, 3]

    # Editing changes the times, never the number; the number is shown read-only.
    page = admin.get("/data/periods/2/edit").get_data(as_text=True)
    assert "readonly" in page and 'name="number"' not in page
    admin.post(
        "/data/periods/2/edit",
        data={"start_time": "09:00", "end_time": "09:45", "number": "7"},
    )
    assert _numbers() == [1, 2, 3]
    assert _rows(Period)[1].end_time.strftime("%H:%M") == "09:45"

    refusal = "Може да се изтрие само последният учебен час"
    assert refusal in admin.get("/data/periods/2/delete").get_data(as_text=True)
    r = admin.post("/data/periods/2/delete", follow_redirects=True)
    assert refusal in r.get_data(as_text=True) and _numbers() == [1, 2, 3]

    admin.post("/data/periods/3/delete")
    assert _numbers() == [1, 2]
    _add_period(admin)  # the freed number is reused: still 1..n
    assert _numbers() == [1, 2, 3]
    admin.post("/data/periods/3/delete")
    admin.post("/data/periods/2/delete")
    admin.post("/data/periods/1/delete")
    assert _numbers() == []
    _add_period(admin)
    assert _numbers() == [1]


def test_room_type_reuses_existing_spelling(admin):
    admin.post(
        "/data/rooms/new", data={"name": "A", "type": "Lab", "concurrent_capacity": "1"}
    )
    admin.post(
        "/data/rooms/new",
        data={"name": "B", "type": " lab ", "concurrent_capacity": "1"},
    )
    assert {r.type for r in _rows(Room)} == {"Lab"}
    assert '<option value="Lab">' in admin.get("/data/rooms/new").get_data(as_text=True)


@pytest.mark.parametrize("key", CASES)
def test_viewer_reads_but_cannot_write_and_anonymous_is_sent_to_login(
    make_client, migrated, key
):
    viewer, anonymous = make_client("viewer"), migrated.test_client()
    r = viewer.get(f"/data/{key}/")
    assert r.status_code == 200
    assert "Добави" not in r.get_data(as_text=True)
    for url in (f"/data/{key}/new", f"/data/{key}/1/edit", f"/data/{key}/1/delete"):
        assert viewer.get(url).status_code == 403
    r = anonymous.get(f"/data/{key}/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_admin_sees_add_edit_and_delete_buttons(admin):
    admin.post("/data/teachers/new", data={"name": "Иван"})
    html = admin.get("/data/teachers/").get_data(as_text=True)
    assert "Добави учител" in html and "Редактирай" in html and "Изтрий" in html


def test_post_without_csrf_token_is_rejected(admin, migrated):
    admin.post("/data/teachers/new", data={"name": "Иван"})
    teacher_id = _rows(Teacher)[0].id
    migrated.config["WTF_CSRF_ENABLED"] = True
    assert admin.post("/data/teachers/new", data={"name": "Петър"}).status_code == 400
    assert admin.post(f"/data/teachers/{teacher_id}/delete").status_code == 400
    assert len(_rows(Teacher)) == 1


# --- classes and groups ---


def test_class_gets_exactly_one_whole_group_and_it_is_protected(admin):
    admin.post("/data/classes/new", data={"name": "10А", "grade": "10"})
    (school_class,) = _rows(SchoolClass)
    (whole,) = _rows(StudentGroup)
    assert whole.is_whole and whole.name == "цял клас"

    for method_url in (
        f"/data/groups/{whole.id}/edit",
        f"/data/groups/{whole.id}/delete",
    ):
        assert admin.get(method_url).status_code == 302
    r = admin.post(f"/data/groups/{whole.id}/delete", follow_redirects=True)
    assert "не може да се променя" in r.get_data(as_text=True)
    assert len(_rows(StudentGroup)) == 1

    # Editing the class does not add another whole-class group.
    admin.post(
        f"/data/classes/{school_class.id}/edit", data={"name": "10Б", "grade": "10"}
    )
    assert len(_rows(StudentGroup)) == 1


def test_extra_groups_can_be_added_renamed_and_deleted(admin):
    admin.post("/data/classes/new", data={"name": "10А", "grade": "10"})
    class_id = _rows(SchoolClass)[0].id
    admin.post(
        f"/data/classes/{class_id}/groups/new",
        data={"name": "Група 1", "split": "групи"},
    )
    group = next(g for g in _rows(StudentGroup) if not g.is_whole)
    assert not group.is_whole and group.split == "групи"
    html = admin.get(f"/data/classes/{class_id}/edit").get_data(as_text=True)
    assert "Група 1" in html and "цял клас" in html

    admin.post(
        f"/data/groups/{group.id}/edit", data={"name": "Първа", "split": "групи"}
    )
    assert db.session.get(StudentGroup, group.id).name == "Първа"
    r = admin.post(f"/data/groups/{group.id}/edit", data={"name": "", "split": ""})
    assert "invalid-feedback" in r.get_data(as_text=True)

    admin.post(f"/data/groups/{group.id}/delete")
    assert [g.is_whole for g in _rows(StudentGroup)] == [True]


# --- rows that are still used ---


@pytest.fixture
def course(admin):
    """A course part that uses a teacher, a subject, a group and (as allowed room) a room."""
    admin.post("/data/teachers/new", data={"name": "Иван"})
    admin.post("/data/subjects/new", data={"name": "Химия"})
    admin.post(
        "/data/rooms/new",
        data={"name": "Лаб", "type": "lab", "concurrent_capacity": "1"},
    )
    admin.post("/data/classes/new", data={"name": "10А", "grade": "10"})
    admin.post("/data/classes/1/groups/new", data={"name": "Група 1", "split": "групи"})
    db.session.expire_all()
    c = Course(periods_per_week=1, block_sizes=[1], label="Химия 10А")
    part = CoursePart(
        course=c,
        subject=db.session.scalars(select(Subject)).one(),
        teachers=[db.session.scalars(select(Teacher)).one()],
        groups=[g for g in db.session.scalars(select(StudentGroup)) if not g.is_whole],
        allowed_room_ids=[db.session.scalars(select(Room)).one().id],
    )
    db.session.add_all([c, part])
    db.session.commit()
    return part


def _refused(admin, url, model):
    r = admin.post(url, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "не може да се изтрие" in html and "Химия 10А" in html
    assert len(_rows(model)) >= 1
    # The confirmation page explains it too and offers no delete button.
    page = admin.get(url).get_data(as_text=True)
    assert "Химия 10А" in page and 'class="btn btn-danger"' not in page


def test_used_teacher_subject_group_class_and_room_are_not_deleted(admin, course):
    teacher, subject = _rows(Teacher)[0], _rows(Subject)[0]
    group = next(g for g in _rows(StudentGroup) if not g.is_whole)
    _refused(admin, f"/data/teachers/{teacher.id}/delete", Teacher)
    _refused(admin, f"/data/subjects/{subject.id}/delete", Subject)
    _refused(admin, f"/data/groups/{group.id}/delete", StudentGroup)
    _refused(admin, f"/data/classes/{group.class_id}/delete", SchoolClass)
    _refused(admin, f"/data/rooms/{_rows(Room)[0].id}/delete", Room)
    assert len(_rows(StudentGroup)) == 2


def test_room_in_past_lessons_can_be_deleted_after_a_notice(admin, course):
    room = _rows(Room)[0]
    part = db.session.scalars(select(CoursePart)).one()
    part.allowed_room_ids = None
    job = SolveJob(status="done")
    db.session.add(job)
    db.session.flush()
    lesson = Lesson(
        job_id=job.id,
        course_id=part.course_id,
        part_id=part.id,
        day=0,
        start_period=1,
        length=1,
        room_id=room.id,
    )
    db.session.add(lesson)
    db.session.commit()
    page = admin.get(f"/data/rooms/{room.id}/delete").get_data(as_text=True)
    assert "там тя ще остане без стая" in page and 'class="btn btn-danger"' in page
    admin.post(f"/data/rooms/{room.id}/delete")
    db.session.expire_all()
    assert _rows(Room) == [] and db.session.get(Lesson, lesson.id).room_id is None


def test_unused_rows_delete_even_when_others_are_used(admin, course):
    admin.post("/data/teachers/new", data={"name": "Свободен"})
    free = next(t for t in _rows(Teacher) if t.name == "Свободен")
    admin.post(f"/data/teachers/{free.id}/delete")
    assert [t.name for t in _rows(Teacher)] == ["Иван"]
