"""BE-16: subject colors and short names."""

from io import BytesIO

from flask_migrate import downgrade, upgrade
from openpyxl import load_workbook
from sqlalchemy import inspect, select

from app import models as m
from app.importing import build_workbook
from app.models import db
from app.subject_colors import PALETTE, subject_color, text_color
from tests.app.test_import import BASE, count, upload, xlsx
from tests.app.test_timetable_page import _solved

NEW = "/data/subjects/new"
EVIL = "red;}body{display:none"


def _subject(name="Химия"):
    return db.session.scalar(select(m.Subject).where(m.Subject.name == name))


def _contrast(fg, bg):
    def lum(h):
        rgb = [int(h[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in rgb]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def test_text_color_is_white_on_dark_black_on_light():
    assert text_color("#000000") == "#ffffff" and text_color("#1f2a44") == "#ffffff"
    assert text_color("#ffffff") == "#000000" and text_color("#edc948") == "#000000"


def test_every_palette_color_and_a_color_sweep_reach_4_5_contrast():
    sweep = [
        f"#{r:02x}{g:02x}{b:02x}"
        for r in range(0, 256, 51)
        for g in range(0, 256, 51)
        for b in range(0, 256, 51)
    ]
    for color in (*PALETTE, *sweep):
        assert _contrast(text_color(color), color) >= 4.5, color


def test_automatic_color_is_picked_by_id():
    assert subject_color(m.Subject(id=1, color=None)) == PALETTE[1]
    assert subject_color(m.Subject(id=13, color=None)) == PALETTE[1]
    assert subject_color(m.Subject(id=1, color="#ABCDEF")) == "#abcdef"
    assert subject_color(m.Subject(id=2, color=EVIL)) == PALETTE[2]  # bad row, no CSS


def test_color_is_validated_and_stored_lowercase(make_client):
    admin = make_client("admin")
    r = admin.post(
        NEW, data={"name": "Химия", "color": "#AABBCC", "short_name": " Хим "}
    )
    assert r.status_code == 302
    assert (_subject().color, _subject().short_name) == ("#aabbcc", "Хим")
    for bad in (EVIL, "#abc", "#12345g", "javascript:1"):
        r = admin.post(NEW, data={"name": "Лошо", "color": bad})
        assert r.status_code == 200 and "Изберете цвят" in r.text, bad
    assert _subject("Лошо") is None
    r = admin.post(
        NEW, data={"name": "Дълго", "color": "#aabbcc", "short_name": "x" * 21}
    )
    assert r.status_code == 200 and _subject("Дълго") is None


def test_automatic_checkbox_clears_the_color(make_client):
    admin = make_client("admin")
    admin.post(NEW, data={"name": "Химия", "color": "#aabbcc"})
    sid = _subject().id
    r = admin.post(
        f"/data/subjects/{sid}/edit",
        data={"name": "Химия", "color": "#aabbcc", "auto_color": "y"},
    )
    assert r.status_code == 302 and _subject().color is None


def test_list_shows_swatch_with_name_and_one_style_block(make_client):
    admin = make_client("admin")
    admin.post(NEW, data={"name": "Химия", "color": "#aabbcc"})
    admin.post(NEW, data={"name": "Физика", "auto_color": "y"})
    html = admin.get("/data/subjects/").text
    assert html.count("<style>") == 1 and "style=" not in html
    for s in db.session.scalars(select(m.Subject)):
        assert f'class="subject-swatch subject-{s.id} ' in html
        assert f".subject-{s.id}{{background-color:{subject_color(s)}" in html
    assert "Химия" in html


def test_form_labels_and_checkbox(make_client):
    html = make_client("admin").get(NEW).text
    assert 'type="color"' in html and "Цвят" in html and "Кратко име (по избор)" in html
    assert "Автоматичен цвят" in html and 'maxlength="20"' in html


def test_timetable_cells_carry_subject_class_style_and_short_name(
    make_client, migrated
):
    _solved()
    first = db.session.scalars(select(m.Subject).order_by(m.Subject.id)).first()
    first.color, first.short_name = "#112233", "Крат"
    db.session.commit()
    client = make_client("viewer")
    html = client.get("/timetable").text
    assert html.count("<style>") == 1
    assert f".subject-{first.id}{{background-color:#112233;color:#ffffff}}" in html
    assert f"subject-{first.id} " in html and "Крат" in html
    # The teacher view shares the grid.
    teacher = db.session.scalars(select(m.Teacher)).first()
    assert "subject-" in client.get(f"/timetable?teacher={teacher.id}").text


def test_split_cell_lines_each_get_their_own_class(make_client, migrated):
    _solved("demo_school.json")
    client = make_client("viewer")
    for c in db.session.scalars(select(m.SchoolClass)):
        html = client.get(f"/timetable?class={c.id}").text
        if html.count("subject-") > 0:
            assert 'class="timetable-lesson border rounded subject-' in html
            return
    raise AssertionError("no lesson cells")


def test_excel_export_fills_single_lesson_cells(make_client, migrated):
    _solved()
    r = make_client("viewer").get("/timetable.xlsx")
    ws = load_workbook(BytesIO(r.data)).worksheets[0]
    filled = [
        c
        for row in ws.iter_rows(min_row=2, min_col=2)
        for c in row
        if c.value and c.fill.fgColor.rgb not in (None, "00000000")
    ]
    assert filled
    c = filled[0]
    bg = "#" + c.fill.fgColor.rgb[-6:].lower()
    assert bg in {s.lower() for s in PALETTE}
    assert (
        c.font.color.rgb[-6:] == text_color(bg)[1:].upper()
        or c.font.color.rgb[-6:].lower() == text_color(bg)[1:]
    )


def test_migration_adds_and_drops_the_columns(app):
    upgrade(directory="migrations", revision="b08a1c2d3e4f")
    assert not {"color", "short_name"} & {
        c["name"] for c in inspect(db.engine).get_columns("subjects")
    }
    upgrade(directory="migrations")
    assert {"color", "short_name"} <= {
        c["name"] for c in inspect(db.engine).get_columns("subjects")
    }
    downgrade(directory="migrations", revision="b08a1c2d3e4f")
    assert not {"color", "short_name"} & {
        c["name"] for c in inspect(db.engine).get_columns("subjects")
    }


def test_import_with_and_without_the_new_columns(make_client):
    admin = make_client("admin")
    # An old file: the original two columns only.
    assert upload(admin, xlsx(BASE)).status_code == 302
    assert count(m.Subject) == 1 and _subject("Математика").color is None
    new = {
        **BASE,
        "Предмети": [
            ["Име", "Тип стая по подразбиране", "Кратко име", "Цвят"],
            ["Математика", "", "Мат", "#AA00BB"],
            ["Химия", "", "", ""],
        ],
    }
    assert upload(admin, xlsx(new)).status_code == 302
    assert (_subject("Математика").short_name, _subject("Математика").color) == (
        "Мат",
        "#aa00bb",
    )
    assert _subject("Химия").color is None
    # Export -> import keeps them.
    buf = BytesIO()
    build_workbook(True).save(buf)
    assert upload(admin, buf.getvalue()).status_code == 302
    assert _subject("Математика").color == "#aa00bb"
    bad = {
        **BASE,
        "Предмети": [
            ["Име", "Тип стая по подразбиране", "Кратко име", "Цвят"],
            ["Математика", "", "", EVIL],
        ],
    }
    r = upload(admin, xlsx(bad))
    assert r.status_code == 200 and "Цвят" in r.text


def test_seed_may_carry_color_and_short_name(migrated):
    from app.cli import load_school
    from tools.seed_to_contract import SEED, load_seed

    seed = load_seed(SEED.parent / "tiny_school.json")
    seed["subjects"][0] |= {"color": "#123456", "short_name": "Б"}
    load_school(seed)
    db.session.commit()
    first = db.session.scalars(select(m.Subject).order_by(m.Subject.id)).first()
    assert (first.color, first.short_name) == ("#123456", "Б")


def test_timetable_and_public_cells_carry_the_text_colour_hook(make_client, migrated):
    _solved()
    sample = db.session.scalars(select(m.Subject)).first()
    sample.color = "#112233"
    db.session.commit()
    html = make_client("admin").get("/timetable").text
    assert "timetable-lesson" in html and "lesson-btn" in html
    # One rule per subject sets the background and the contrasting text colour together.
    for s in db.session.scalars(select(m.Subject)):
        bg = subject_color(s)
        assert (
            f".subject-{s.id}{{background-color:{bg};color:{text_color(bg)}}}" in html
        )


def test_text_color_picks_the_higher_contrast_on_mid_tones():
    for color in ("#b07aa1", "#9c755f", "#4e79a7", "#e15759", "#edc948"):
        other = "#ffffff" if text_color(color) == "#000000" else "#000000"
        assert _contrast(text_color(color), color) >= _contrast(other, color), color
