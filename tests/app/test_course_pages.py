"""FE-05b: courses (curriculum) and their parts."""

import pytest
from sqlalchemy import select

from app.models import (
    Constraint,
    Course,
    CoursePart,
    Lesson,
    SolveJob,
    StudentGroup,
    Subject,
    Teacher,
    db,
)
from app.solver_input import build_solver_input


@pytest.fixture
def admin(make_client):
    return make_client("admin")


@pytest.fixture
def school(admin):
    """Two classes (10А with one extra group), teachers, subjects, rooms, a 4-period day."""
    post = admin.post
    for name in ("Иван", "Мария", "Петър"):
        post("/data/teachers/new", data={"name": name})
    post("/data/subjects/new", data={"name": "Химия", "default_room_type": "lab"})
    post("/data/subjects/new", data={"name": "БЕЛ"})
    post(
        "/data/rooms/new",
        data={"name": "Лаб 1", "type": "lab", "concurrent_capacity": "1"},
    )
    post(
        "/data/rooms/new",
        data={"name": "Стая 1", "type": "regular", "concurrent_capacity": "1"},
    )
    post("/data/classes/new", data={"name": "10А", "grade": "10"})
    post("/data/classes/new", data={"name": "10Б", "grade": "10"})
    post("/data/classes/1/groups/new", data={"name": "Група 1", "split": "групи"})
    post("/data/classes/1/groups/new", data={"name": "Група 2", "split": "групи"})
    for i in range(4):
        post(
            "/data/periods/new",
            data={"start_time": f"0{8 + i}:00", "end_time": f"0{8 + i}:40"},
        )
    db.session.expire_all()

    def ids(model, **kw):
        return {r.name: r.id for r in db.session.scalars(select(model).filter_by(**kw))}

    groups = {
        f"{g.school_class.name}/{g.name}": g.id
        for g in db.session.scalars(select(StudentGroup))
    }
    return {
        "teachers": ids(Teacher),
        "subjects": ids(Subject),
        "groups": groups,
    }


def _course(admin, label="Химия 10А", per_week="2", blocks="2"):
    r = admin.post(
        "/data/courses/new",
        data={"label": label, "periods_per_week": per_week, "block_sizes": blocks},
    )
    assert r.status_code == 302
    return db.session.scalars(select(Course).order_by(Course.id.desc())).first()


def _part_data(school, **over):
    data = {
        "subject": school["subjects"]["БЕЛ"],
        "groups": [school["groups"]["10Б/цял клас"]],
        "teachers": [school["teachers"]["Иван"]],
    }
    data.update(over)
    return data


def test_create_goes_to_edit_page_with_parts_section(admin, school):
    r = admin.post(
        "/data/courses/new",
        data={"label": "X", "periods_per_week": "3", "block_sizes": "2, 1"},
        follow_redirects=True,
    )
    html = r.get_data(as_text=True)
    assert "Редактиране на учебен план" in html and "Добави част" in html
    assert "поне една част" in html
    assert db.session.scalars(select(Course)).one().block_sizes == [2, 1]


def test_edit_form_shows_blocks_as_text(admin, school):
    course = _course(admin, per_week="3", blocks="2 + 1")
    html = admin.get(f"/data/courses/{course.id}/edit").get_data(as_text=True)
    assert 'value="2, 1"' in html


@pytest.mark.parametrize(
    "per_week, blocks, message",
    [
        ("3", "2, 2", "Сборът на блоковете"),
        ("5", "5", "по-дълъг от учебния ден"),
        ("2", "две", "положителни цели числа"),
        ("2", "0, 2", "положителни цели числа"),
        ("2", "", "задължително"),
        ("", "2", "задължително"),
        ("0", "2", "поне 1"),
    ],
)
def test_course_validation(admin, school, per_week, blocks, message):
    r = admin.post(
        "/data/courses/new", data={"periods_per_week": per_week, "block_sizes": blocks}
    )
    assert r.status_code == 200 and message in r.get_data(as_text=True)
    assert db.session.scalars(select(Course)).all() == []


def test_unlabelled_course_gets_a_name_in_the_list(admin, school):
    course = _course(admin, label="")
    html = admin.get("/data/courses/").get_data(as_text=True)
    assert f"учебен план №{course.id}" in html


def test_part_add_edit_delete(admin, school):
    course = _course(admin)
    url = f"/data/courses/{course.id}/parts/new"
    assert admin.get(url).status_code == 200
    data = _part_data(
        school,
        subject=school["subjects"]["Химия"],
        groups=[school["groups"]["10А/Група 1"], school["groups"]["10А/Група 2"]],
        needs_room="y",
        room_type="lab",
        allowed_room_ids=[1],
    )
    # Two groups of the same split can share a part only if they do not conflict:
    # same class + same split is fine for different groups, a part is a set.
    r = admin.post(url, data=data, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Частта е добавена" in html and "10А — Група 1" in html and "Иван" in html
    db.session.expire_all()
    part = db.session.scalars(select(CoursePart)).one()
    assert part.needs_room and part.room_type == "lab"
    assert part.allowed_room_ids == [1] and len(part.groups) == 2

    page = admin.get(f"/data/parts/{part.id}/edit").get_data(as_text=True)
    assert 'value="lab"' in page and "checked" in page

    data["teachers"] = [school["teachers"]["Мария"]]
    del data["needs_room"]  # room fields are cleared without needs_room
    r = admin.post(f"/data/parts/{part.id}/edit", data=data, follow_redirects=True)
    assert "Частта е запазена" in r.get_data(as_text=True)
    db.session.expire_all()
    part = db.session.scalars(select(CoursePart)).one()
    assert [t.name for t in part.teachers] == ["Мария"]
    assert not part.needs_room and part.room_type is None
    assert part.allowed_room_ids is None

    assert admin.get(f"/data/parts/{part.id}/delete").status_code == 200
    r = admin.post(f"/data/parts/{part.id}/delete", follow_redirects=True)
    assert "Частта е изтрита" in r.get_data(as_text=True)
    assert db.session.scalars(select(CoursePart)).all() == []
    assert db.session.scalars(select(Course)).one()


@pytest.mark.parametrize(
    "over, message",
    [
        ({"groups": []}, "поне една група"),
        ({"teachers": []}, "поне един учител"),
        ({"subject": ""}, "Изберете предмет"),
        ({"subject": "999"}, "Изберете предмет"),
        ({"teachers": ["999"]}, "Изберете учители"),
        ({"needs_room": "y", "room_type": "gym"}, "Няма стая, която"),
        (
            {"needs_room": "y", "room_type": "lab", "allowed_room_ids": [2]},
            "Няма стая, която",
        ),
        ({"needs_room": "y", "subject": 1}, None),  # subject default "lab" exists
    ],
)
def test_part_validation(admin, school, over, message):
    course = _course(admin)
    r = admin.post(
        f"/data/courses/{course.id}/parts/new", data=_part_data(school, **over)
    )
    if message is None:
        assert r.status_code == 302
        return
    assert r.status_code == 200 and message in r.get_data(as_text=True)
    assert db.session.scalars(select(CoursePart)).all() == []


def test_subject_default_room_type_is_used_when_type_is_blank(admin, school):
    # Химия defaults to "lab"; the only lab is excluded by allowed rooms -> no match.
    course = _course(admin)
    data = _part_data(
        school,
        subject=school["subjects"]["Химия"],
        needs_room="y",
        allowed_room_ids=[2],
    )
    r = admin.post(f"/data/courses/{course.id}/parts/new", data=data)
    assert "Няма стая, която" in r.get_data(as_text=True)


def test_parts_of_one_course_may_not_share_teachers_or_conflicting_groups(
    admin, school
):
    course = _course(admin)
    url = f"/data/courses/{course.id}/parts/new"
    g1, g2 = school["groups"]["10А/Група 1"], school["groups"]["10А/Група 2"]
    whole_a, whole_b = (
        school["groups"]["10А/цял клас"],
        school["groups"]["10Б/цял клас"],
    )
    ivan, maria = school["teachers"]["Иван"], school["teachers"]["Мария"]
    ok = admin.post(url, data=_part_data(school, groups=[g1], teachers=[ivan]))
    assert ok.status_code == 302

    same_teacher = admin.post(
        url, data=_part_data(school, groups=[g2], teachers=[ivan])
    )
    assert "Вече е в друга част" in same_teacher.get_data(as_text=True)

    whole_vs_group = admin.post(
        url, data=_part_data(school, groups=[whole_a], teachers=[maria])
    )
    assert "общи ученици" in whole_vs_group.get_data(as_text=True)

    inside = admin.post(
        url, data=_part_data(school, groups=[whole_a, g2], teachers=[maria])
    )
    assert "общи ученици" in inside.get_data(as_text=True)

    # Another class and another group of the same split are fine (two subgroups at once).
    assert (
        admin.post(
            url, data=_part_data(school, groups=[g2], teachers=[maria])
        ).status_code
        == 302
    )
    assert (
        admin.post(
            url,
            data=_part_data(
                school, groups=[whole_b], teachers=[school["teachers"]["Петър"]]
            ),
        ).status_code
        == 302
    )
    assert len(db.session.scalars(select(CoursePart)).all()) == 3

    # Editing a part does not clash with itself.
    part = db.session.scalars(select(CoursePart).order_by(CoursePart.id)).first()
    r = admin.post(
        f"/data/parts/{part.id}/edit",
        data=_part_data(school, groups=[g1], teachers=[ivan]),
    )
    assert r.status_code == 302


def test_entered_curriculum_gives_a_valid_solver_input(admin, school):
    course = _course(admin, per_week="3", blocks="2, 1")
    data = _part_data(
        school,
        subject=school["subjects"]["Химия"],
        groups=[school["groups"]["10А/цял клас"]],
        needs_room="y",
        room_type="lab",
    )
    assert (
        admin.post(f"/data/courses/{course.id}/parts/new", data=data).status_code == 302
    )
    db.session.expire_all()
    inp = build_solver_input(db.session)
    assert inp.problems() == []
    (c,) = inp.courses
    assert c.block_sizes == (2, 1) and c.parts[0].room_type == "lab"


def test_course_without_parts_is_reported_not_hidden(admin, school):
    _course(admin)
    codes = {p.code for p in build_solver_input(db.session).problems()}
    assert "empty_course" in codes


def test_used_course_is_not_deleted_and_lessons_are_noted(admin, school):
    course = _course(admin)
    con = Constraint(
        type="fixed_slot",
        params={"course_id": course.id, "blocks": []},
        status="confirmed",
    )
    db.session.add(con)
    db.session.commit()
    r = admin.post(f"/data/courses/{course.id}/delete", follow_redirects=True)
    assert "не може да се изтрие" in r.get_data(as_text=True)
    assert f"№{con.id}" in r.get_data(as_text=True)
    assert db.session.get(Course, course.id)

    db.session.delete(con)
    db.session.commit()
    admin.post(f"/data/courses/{course.id}/parts/new", data=_part_data(school))
    part = db.session.scalars(select(CoursePart)).one()
    job = SolveJob(status="done")
    db.session.add(job)
    db.session.flush()
    db.session.add(
        Lesson(
            job_id=job.id,
            course_id=course.id,
            part_id=part.id,
            day=1,
            start_period=1,
            length=2,
        )
    )
    db.session.commit()
    page = admin.get(f"/data/courses/{course.id}/delete").get_data(as_text=True)
    assert "вече генерирани разписания" in page and 'class="btn btn-danger"' in page
    admin.post(f"/data/courses/{course.id}/delete")
    db.session.expire_all()
    assert db.session.scalars(select(Course)).all() == []
    assert db.session.scalars(select(Lesson)).all() == []


def test_deleting_a_course_deletes_its_parts(admin, school):
    course = _course(admin)
    admin.post(f"/data/courses/{course.id}/parts/new", data=_part_data(school))
    page = admin.get(f"/data/courses/{course.id}/delete").get_data(as_text=True)
    assert 'class="btn btn-danger"' in page
    admin.post(f"/data/courses/{course.id}/delete")
    db.session.expire_all()
    assert db.session.scalars(select(Course)).all() == []
    assert db.session.scalars(select(CoursePart)).all() == []


def test_used_teacher_cannot_be_deleted_after_entering_a_part(admin, school):
    course = _course(admin)
    admin.post(f"/data/courses/{course.id}/parts/new", data=_part_data(school))
    r = admin.post(
        f"/data/teachers/{school['teachers']['Иван']}/delete", follow_redirects=True
    )
    assert "не може да се изтрие" in r.get_data(as_text=True)


def test_viewer_sees_course_pages_read_only(make_client, admin, school):
    course = _course(admin)
    admin.post(f"/data/courses/{course.id}/parts/new", data=_part_data(school))
    viewer = make_client("viewer")
    html = viewer.get("/data/courses/").get_data(as_text=True)
    assert "Добави" not in html and "БЕЛ" in html
    for url in (
        f"/data/courses/{course.id}/parts/new",
        "/data/parts/1/edit",
        "/data/parts/1/delete",
    ):
        assert viewer.get(url).status_code == 403
        assert viewer.post(url).status_code == 403


def test_navbar_lists_courses_and_marks_it_active(admin, school):
    html = admin.get("/data/courses/").get_data(as_text=True)
    assert (
        'aria-current="page"\n                       href="/data/courses/">Учебен план'
        in html
    )
