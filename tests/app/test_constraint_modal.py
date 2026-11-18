"""FE-20: multi-target constraint modal (HTMX partial) and its no-JS fallback."""

import re

import pytest
from sqlalchemy import select

from app import models as m
from app.cli import load_school
from app.constraint_forms import FORMS, TARGETS
from app.models import db
from tools.seed_to_contract import SEED, load_seed

HX = {"HX-Request": "true"}


@pytest.fixture
def admin(make_client):
    load_school(load_seed(SEED.parent / "tiny_school.json"))
    db.session.commit()
    return make_client("admin")


def _all(model):
    return db.session.scalars(select(model).order_by(model.id)).all()


def _constraints():
    return db.session.scalars(select(m.Constraint).order_by(m.Constraint.id)).all()


def _csrf(client, kind="teacher_max_gaps"):
    html = client.get(f"/constraints/new/{kind}").get_data(as_text=True)
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)


def test_modal_partial_lists_the_right_entities_per_type(admin):
    teachers, subjects = _all(m.Teacher), _all(m.Subject)
    assert len(teachers) >= 2
    r = admin.get("/constraints/new?kind=teacher_max_gaps", headers=HX)
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "<html" not in html
    assert (
        "data-picker-search" in html and "Избери всички" in html and "Изчисти" in html
    )
    for t in teachers:
        assert 'name="teacher" id="target-teacher-' in html
        assert t.name in html
    assert f"Избрани: 0 от {len(teachers)}" in html

    html = admin.get(
        "/constraints/new?kind=subject_unavailable_slots", headers=HX
    ).get_data(as_text=True)
    assert 'name="subject_id"' in html and subjects[0].name in html
    assert 'name="teacher"' not in html

    # no picker for a global type
    html = admin.get(
        "/constraints/new?kind=periods_reserved_for_subjects", headers=HX
    ).get_data(as_text=True)
    assert "data-picker-search" not in html
    assert "Единствените предмети" in html


def test_every_type_opens_in_the_modal(admin):
    for kind in FORMS:
        r = admin.get(f"/constraints/new?kind={kind}", headers=HX)
        assert r.status_code == 200, kind
        assert ("data-picker-search" in r.get_data(as_text=True)) == (kind in TARGETS)
    assert admin.get("/constraints/new?kind=nope", headers=HX).status_code == 404


def test_type_picker_without_js_redirects_to_full_page(admin):
    r = admin.get("/constraints/new?kind=no_gaps")
    assert r.status_code == 302 and r.headers["Location"].endswith(
        "/constraints/new/no_gaps"
    )
    html = admin.get("/constraints/").get_data(as_text=True)
    assert 'id="constraint-kind"' in html and 'id="constraint-modal"' in html


def test_many_targets_create_one_constraint_each(admin):
    teachers = _all(m.Teacher)
    r = admin.post(
        "/constraints/new/teacher_max_gaps/many",
        data={"teacher": [t.id for t in teachers], "max": "1", "hard": "y"},
        headers=HX,
    )
    assert r.status_code == 200 and r.headers["HX-Redirect"].endswith("/constraints/")
    rows = _constraints()
    assert len(rows) == len(teachers)
    assert [c.params["teacher_id"] for c in rows] == [t.id for t in teachers]
    assert {
        (c.type, c.hard, c.weight, c.params["max"], c.status, c.source) for c in rows
    } == {("teacher_max_gaps", True, None, 1, "confirmed", "manual")}
    html = admin.get("/constraints/").get_data(as_text=True)
    assert f"Добавени са {len(teachers)} ограничения." in html


def test_single_target_flash_uses_singular(admin):
    t = _all(m.Teacher)[0]
    admin.post(
        "/constraints/new/teacher_max_gaps/many",
        data={"teacher": [t.id], "max": "1", "hard": "y"},
        headers=HX,
    )
    assert "Добавено е 1 ограничение." in admin.get("/constraints/").get_data(
        as_text=True
    )


def test_scope_type_accepts_many_scopes(admin):
    classes = _all(m.SchoolClass)
    scopes = [f"class:{c.id}" for c in classes]
    admin.post(
        "/constraints/new/no_gaps/many",
        data={"scope": scopes + ["all"], "hard": "y"},
        headers=HX,
    )
    assert [c.params["scope"] for c in _constraints()] == [
        {"class_id": c.id} for c in classes
    ] + ["all"]


def test_one_invalid_target_saves_nothing_and_shows_error(admin):
    teachers = _all(m.Teacher)
    r = admin.post(
        "/constraints/new/teacher_max_gaps/many",
        data={"teacher": [teachers[0].id, 99999], "max": "1", "hard": "y"},
        headers=HX,
    )
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "HX-Redirect" not in r.headers
    assert _constraints() == []
    assert "Нищо не е запазено" in html and "№99999" in html
    # the input survives: the valid target stays ticked, the value stays
    assert re.search(rf'value="{teachers[0].id}" checked', html)
    assert 'value="1"' in html


def test_reference_check_failure_is_all_or_nothing(admin):
    t = _all(m.Teacher)[0]
    r = admin.post(
        "/constraints/new/teacher_available_days/many",
        data={"teacher": [t.id], "days": ["9"], "hard": "y"},
        headers=HX,
    )
    assert "Изберете дни от списъка." in r.get_data(as_text=True)
    assert _constraints() == []


def test_field_errors_and_no_target_are_reported(admin):
    t = _all(m.Teacher)[0]
    r = admin.post(
        "/constraints/new/teacher_max_gaps/many",
        data={"teacher": [t.id], "hard": "y"},  # max missing
        headers=HX,
    )
    assert "is-invalid" in r.get_data(as_text=True) and _constraints() == []
    r = admin.post(
        "/constraints/new/teacher_max_gaps/many",
        data={"max": "1", "hard": "y"},
        headers=HX,
    )
    assert "Изберете поне един учител." in r.get_data(as_text=True)
    assert _constraints() == []


def test_type_without_picker_creates_one(admin):
    s = _all(m.Subject)[0]
    r = admin.post(
        "/constraints/new/periods_reserved_for_subjects/many",
        data={"periods": ["1"], "subject_ids": [s.id], "hard": "y"},
        headers=HX,
    )
    assert "HX-Redirect" in r.headers and len(_constraints()) == 1


def test_soft_weight_is_copied_to_every_row(admin):
    teachers = _all(m.Teacher)
    admin.post(
        "/constraints/new/teacher_max_gaps/many",
        data={"teacher": [t.id for t in teachers], "max": "0", "weight": "4"},
        headers=HX,
    )
    assert {(c.hard, c.weight) for c in _constraints()} == {(False, 4)}


def test_csrf_is_enforced_on_the_modal_post(admin, migrated):
    migrated.config["WTF_CSRF_ENABLED"] = True
    t = _all(m.Teacher)[0]
    r = admin.post(
        "/constraints/new/teacher_max_gaps/many",
        data={"teacher": [t.id], "max": "1", "hard": "y"},
        headers=HX,
    )
    assert _constraints() == [] and "csrf" not in r.headers.get("HX-Redirect", "")
    token = _csrf(admin)
    admin.post(
        "/constraints/new/teacher_max_gaps/many",
        data={"teacher": [t.id], "max": "1", "hard": "y", "csrf_token": token},
        headers=HX,
    )
    assert len(_constraints()) == 1


def test_viewer_cannot_use_the_modal(make_client, admin):
    viewer = make_client("viewer")
    assert viewer.get("/constraints/new?kind=no_gaps", headers=HX).status_code == 403
    r = viewer.post(
        "/constraints/new/no_gaps/many", data={"scope": ["all"]}, headers=HX
    )
    assert r.status_code == 403 and _constraints() == []
    html = viewer.get("/constraints/").get_data(as_text=True)
    assert "constraint-modal" not in html and "constraint-kind" not in html


def test_non_htmx_post_to_modal_route_is_refused(admin):
    r = admin.post("/constraints/new/no_gaps/many", data={"scope": ["all"]})
    assert r.status_code == 400 and _constraints() == []


def test_full_page_path_still_works_without_js(admin):
    t = _all(m.Teacher)[0]
    html = admin.get("/constraints/new/teacher_max_gaps").get_data(as_text=True)
    assert 'name="teacher"' in html and "data-picker" not in html
    r = admin.post(
        "/constraints/new/teacher_max_gaps",
        data={"teacher": t.id, "max": "1", "hard": "y"},
    )
    assert r.status_code == 302 and len(_constraints()) == 1
