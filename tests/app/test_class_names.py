"""BE-15: standard class names (8А, 12Г) and conversion of the school's class codes."""

import pytest

from app.class_names import class_name_from_code, normalize_class_name
from app.models import SchoolClass, db
from tests.app.test_import import BASE, snapshot, upload, xlsx


@pytest.mark.parametrize(
    "code, grade, name",
    [("251", 8, "8А"), ("234", 10, "10Г"), ("221", 11, "11А"), ("215", 12, "12Д")]
    + [("256", 8, "8Е"), ("240", 9, None), ("250", 8, None)],
)
def test_code_conversion_2025(code, grade, name):
    if name is None:  # parallel 0 is never valid
        with pytest.raises(ValueError, match="паралелен"):
            class_name_from_code(code, 2025)
    else:
        assert class_name_from_code(code, 2025) == (grade, name)


@pytest.mark.parametrize("code", ["261", "201", "2a1", "34", "1234"])
def test_code_out_of_range_is_an_error_not_a_guess(code):
    with pytest.raises(ValueError, match="Кодът"):
        class_name_from_code(code, 2025)


def test_parallel_above_ten_and_letters():
    assert class_name_from_code("259", 2025) == (8, "8И")
    with pytest.raises(ValueError):
        class_name_from_code("25", 2025)


@pytest.mark.parametrize(
    "raw, name", [("10 г", "10Г"), ("10a", "10А"), (" 8 b ", "8В"), ("12Г", "12Г")]
)
def test_normalize(raw, name):
    assert normalize_class_name(raw) == name


def _post(admin, **data):
    return admin.post("/data/classes/new", data=data)


def test_class_form_rejects_the_school_code(make_client):
    admin = make_client("admin")
    r = _post(admin, name="234", grade="")
    assert "Името на класа трябва да е във вида 8А или 12Г." in r.get_data(as_text=True)
    assert db.session.query(SchoolClass).count() == 0


def test_class_form_normalizes_and_fills_grade(make_client):
    admin = make_client("admin")
    assert _post(admin, name="10 a", grade="").status_code == 302
    c = db.session.query(SchoolClass).one()
    assert (c.name, c.grade) == ("10А", 10)


def test_class_form_rejects_grade_mismatch(make_client):
    r = _post(make_client("admin"), name="10А", grade="9")
    assert "не съвпада" in r.get_data(as_text=True)


def _coded():
    sheets = {k: [list(r) for r in v] for k, v in BASE.items()}
    sheets["Класове"] = [["Име", "Година на обучение"], ["251", ""]]
    sheets["Групи"][1][0] = "251"
    sheets["Учебен план"][1][5] = "251 — Първа група"
    return sheets


def test_import_converts_codes_and_preview_saves_nothing(make_client):
    admin = make_client("admin")
    r = upload(admin, xlsx(_coded()), school_year="2025", preview="y")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "251 → 8А" in html
    assert snapshot()["classes"] == []
    assert upload(admin, xlsx(_coded()), school_year="2025").status_code == 302
    snap = snapshot()
    assert snap["classes"] == [("8А", 8)]
    assert snap["courses"][0][3][0][1] == ["8А — Първа група"]


def test_import_code_out_of_range_for_the_year(make_client):
    r = upload(make_client("admin"), xlsx(_coded()), school_year="2030")
    html = r.get_data(as_text=True)
    assert "Нищо не е запазено" in html and "не съответства" in html
    assert snapshot()["classes"] == []


def test_new_class_form_opens(make_client):
    """The empty form passed None to the name filter and crashed with a 500."""
    assert normalize_class_name(None) is None
    page = make_client("admin").get("/data/classes/new")
    assert page.status_code == 200 and 'name="name"' in page.get_data(as_text=True)
