from app import create_app
from app import models as m
from app.cli import load_school
from app.models import db
from tools.seed_to_contract import SEED, load_seed


def test_index_shows_page():
    response = create_app().test_client().get("/")
    assert response.status_code == 200
    assert "SATurn" in response.get_data(as_text=True)


# --- dashboard (logged in) --------------------------------------------------


def _school():
    load_school(load_seed(SEED.parent / "tiny_school.json"))
    db.session.commit()


def test_guest_sees_login_and_no_numbers(migrated):
    html = migrated.test_client().get("/").get_data(as_text=True)
    assert "Вход" in html and "Стъпки" not in html and "Изгради график" not in html


def test_admin_dashboard_shows_counts_steps_and_build_button(make_client):
    admin = make_client("admin")
    _school()
    db.session.add(
        m.Constraint(type="no_gaps", params={"scope": "all"}, status="proposed")
    )
    db.session.commit()
    html = admin.get("/").get_data(as_text=True)
    assert "Изгради график" in html and 'name="csrf_token"' in html
    assert "Още няма график" in html
    teachers = db.session.scalar(db.select(db.func.count()).select_from(m.Teacher))
    assert f">{teachers}<" in html.replace(" ", "").replace("\n", "")
    # Data is done, a proposal waits: step 2 is the next one and says how many.
    assert html.count('aria-current="step"') == 1
    assert "1 чакат потвърждение" in html
    assert "Стъпка 2 · следваща" in html


def test_dashboard_shows_latest_job_status(make_client):
    admin = make_client("admin")
    _school()
    db.session.add(
        m.SolveJob(status="finished", solver_status="OPTIMAL", time_limit_s=5)
    )
    db.session.commit()
    html = admin.get("/").get_data(as_text=True)
    assert "Последен график · изчисление №1" in html and "Оптимален" in html
    assert "Excel" in html


def test_viewer_sees_only_published_jobs_and_no_admin_actions(make_client):
    viewer = make_client("viewer")
    _school()
    db.session.add(
        m.SolveJob(status="finished", solver_status="OPTIMAL", time_limit_s=5)
    )
    db.session.commit()
    html = viewer.get("/").get_data(as_text=True)
    assert "Изгради график" not in html and "Опишете правило с думи" not in html
    assert "Графикът ще се появи тук" in html  # the job is not published


def test_quick_rule_box_only_with_llm(make_client, migrated):
    admin = make_client("admin")
    assert "Опишете правило с думи" not in admin.get("/").get_data(as_text=True)
    migrated.config["LLM_ENABLED"] = True
    assert "Опишете правило с думи" in admin.get("/").get_data(as_text=True)
