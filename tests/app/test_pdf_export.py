import os
import sys

import pytest
from sqlalchemy import select

from app import models as m
from app.models import db
from tests.app.test_timetable_page import _solved

try:
    import weasyprint
except (ImportError, OSError):
    weasyprint = None

# CI sets REQUIRE_PDF=1: a missing WeasyPrint there is a failure, never a silent skip.
if os.environ.get("REQUIRE_PDF"):
    assert weasyprint, "REQUIRE_PDF is set but weasyprint cannot be imported"

needs_pdf = pytest.mark.skipif(
    weasyprint is None, reason="WeasyPrint/GTK is not available on this machine"
)


@pytest.fixture
def rendered_html(monkeypatch):
    """The HTML each PDF is made from: PDF text is font-subset, so check names here."""
    seen = []
    if weasyprint:
        real = weasyprint.HTML

        def spy(*a, **kw):
            seen.append(kw["string"])
            return real(*a, **kw)

        monkeypatch.setattr(weasyprint, "HTML", spy)
    return seen


def _check(resp):
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.data.startswith(b"%PDF") and len(resp.data) > 2000


@needs_pdf
def test_every_pdf_route_returns_a_pdf_with_the_name(
    make_client, migrated, rendered_html
):
    _solved()
    klass = db.session.scalars(select(m.SchoolClass)).first()
    teacher = db.session.scalars(select(m.Teacher)).first()
    room = db.session.scalars(select(m.Room)).first()
    for role in ("admin", "viewer"):
        client = make_client(role)
        for url, name in (
            (f"/timetable.pdf?class={klass.id}", klass.name),
            (f"/timetable.pdf?teacher={teacher.id}", teacher.name),
            (f"/timetable.pdf?room={room.id}", room.name),
            ("/overview.pdf", klass.name),
        ):
            rendered_html.clear()
            _check(client.get(url))
            assert name in rendered_html[0], url
    assert "Понеделник" in rendered_html[0]


@needs_pdf
def test_pdf_without_timetable_redirects(make_client, migrated):
    r = make_client("viewer").get("/overview.pdf")
    assert r.status_code == 302 and "/timetable" in r.headers["Location"]


def test_pdf_requires_login(migrated):
    for url in ("/timetable.pdf", "/overview.pdf"):
        assert migrated.test_client().get(url).status_code == 302


def test_missing_weasyprint_flashes_instead_of_500(make_client, migrated, monkeypatch):
    _solved()
    monkeypatch.setitem(sys.modules, "weasyprint", None)  # makes the import fail
    client = make_client("viewer")
    assert client.get("/timetable.pdf").status_code == 302
    assert "PDF изтеглянето не е налично" in client.get("/timetable").text
