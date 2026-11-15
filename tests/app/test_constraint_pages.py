"""FE-06: constraint form, status list and the confirmed-only rule."""

import pytest
from sqlalchemy import select

from app import models as m
from app.cli import load_school
from app.models import db
from app.solver_input import build_solver_input
from tools.seed_to_contract import SEED, load_seed


@pytest.fixture
def admin(make_client):
    load_school(load_seed(SEED.parent / "tiny_school.json"))
    db.session.commit()
    return make_client("admin")


def _first(model):
    return db.session.scalars(select(model).order_by(model.id)).first()


def _add(status="proposed", **kw):
    c = m.Constraint(
        type="no_gaps", params={"scope": "all"}, hard=True, status=status, **kw
    )
    db.session.add(c)
    db.session.commit()
    return c


def test_only_confirmed_constraints_reach_the_solver(admin):
    ids = {s: _add(s).id for s in ("proposed", "confirmed", "rejected")}
    got = [k.id for k in build_solver_input(db.session).constraints]
    assert got == [ids["confirmed"]]


def test_every_type_can_be_added_by_hand(admin):
    teacher, course = _first(m.Teacher), _first(m.Course)
    subject, school_class = _first(m.Subject), _first(m.SchoolClass)
    forms = {
        "teacher_available_days": {"teacher": teacher.id, "days": ["1", "2"]},
        "teacher_unavailable_slots": {"teacher": teacher.id, "slots": "1:1, 2:2"},
        "room_type_required": {"subject_id": subject.id, "room_type": "lab"},
        "max_lessons_per_day": {"scope": f"class:{school_class.id}", "max": "6"},
        "no_gaps": {"scope": "all"},
        "fixed_slot": {
            "course_id": course.id,
            "blocks": ", ".join(f"{i}:1" for i, _ in enumerate(course.block_sizes, 1)),
        },
    }
    for kind, data in forms.items():
        r = admin.post(f"/constraints/new/{kind}", data={"hard": "y", **data})
        assert r.status_code == 302, (kind, r.get_data(as_text=True)[-2000:])
    rows = db.session.scalars(select(m.Constraint)).all()
    assert {c.type for c in rows} == set(forms)
    assert {c.status for c in rows} == {"confirmed"}
    assert build_solver_input(db.session).problems() == []


def test_invalid_input_is_refused(admin):
    teacher = _first(m.Teacher)
    # soft without weight, day outside the school week, unknown reference
    for data in (
        {"teacher": teacher.id, "days": ["1"]},
        {"teacher": teacher.id, "days": ["9"], "hard": "y"},
        {"teacher": 9999, "days": ["1"], "hard": "y"},
    ):
        r = admin.post("/constraints/new/teacher_available_days", data=data)
        assert r.status_code == 200
    r = admin.post(
        "/constraints/new/fixed_slot",
        data={
            "course_id": _first(m.Course).id,
            "blocks": "1:1,1:1,1:1,1:1",
            "hard": "y",
        },
    )
    assert "не съответства на данните" in r.get_data(as_text=True)
    assert db.session.scalars(select(m.Constraint)).all() == []
    assert admin.get("/constraints/new/nope").status_code == 404


def test_soft_constraint_keeps_its_weight(admin):
    admin.post("/constraints/new/no_gaps", data={"scope": "all", "weight": "3"})
    c = _first(m.Constraint)
    assert (c.hard, c.weight) == (False, 3)


def test_confirm_reject_delete_via_htmx(admin):
    c = _add(original_text="Господин Иванов не работи в петък")
    hx = {"HX-Request": "true"}
    html = admin.get("/constraints/").get_data(as_text=True)
    assert "Господин Иванов не работи в петък" in html and "Предложено" in html

    r = admin.post(
        f"/constraints/{c.id}/status", data={"status": "confirmed"}, headers=hx
    )
    assert "Потвърдено" in r.get_data(as_text=True) and "<html" not in r.get_data(
        as_text=True
    )
    assert db.session.get(m.Constraint, c.id).status == "confirmed"
    admin.post(f"/constraints/{c.id}/status", data={"status": "rejected"}, headers=hx)
    db.session.expire_all()
    assert db.session.get(m.Constraint, c.id).status == "rejected"
    assert (
        admin.post(f"/constraints/{c.id}/status", data={"status": "x"}).status_code
        == 400
    )

    assert admin.post(f"/constraints/{c.id}/delete", headers=hx).get_data() == b""
    assert db.session.get(m.Constraint, c.id) is None


def test_plain_post_redirects_without_htmx(admin):
    c = _add()
    r = admin.post(f"/constraints/{c.id}/status", data={"status": "confirmed"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/constraints/")


def test_viewer_reads_but_cannot_change(make_client, admin):
    c = _add()
    viewer = make_client("viewer")
    html = viewer.get("/constraints/").get_data(as_text=True)
    assert "Потвърди" not in html and "Добави" not in html
    assert (
        viewer.post(
            f"/constraints/{c.id}/status", data={"status": "confirmed"}
        ).status_code
        == 403
    )
    assert make_client().get("/constraints/").status_code == 302


def test_malformed_params_do_not_break_the_list(admin):
    db.session.add(m.Constraint(type="fixed_slot", params={"oops": 1}, hard=True))
    db.session.commit()
    assert admin.get("/constraints/").status_code == 200


def _box(client, app, enabled):
    app.config["LLM_ENABLED"] = enabled
    return b'name="text"' in client.get("/constraints/").data


def test_request_box_only_for_admin_when_llm_on(admin, make_client, migrated):
    assert _box(admin, migrated, True)
    assert not _box(admin, migrated, False)
    assert not _box(make_client("viewer"), migrated, True)


def test_page_post_lands_ai_rows_as_proposed(admin, migrated, monkeypatch):
    from app import llm

    migrated.config.update(LLM_ENABLED=True)
    c = m.Constraint(
        type="no_gaps",
        params={"scope": "all"},
        hard=True,
        status="proposed",
        source="llm",
        original_text="без прозорци",
    )

    def fake(text):
        db.session.add(c)
        db.session.commit()
        return [c], []

    monkeypatch.setattr(llm, "propose_constraints", fake)
    html = admin.post(
        "/constraints/propose", data={"text": "без прозорци"}, follow_redirects=True
    ).get_data(as_text=True)
    assert "Предложено" in html and "ИИ" in html and "без прозорци" in html
