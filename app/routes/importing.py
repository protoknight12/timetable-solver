"""FE-13: import school data from an Excel/CSV template (admin only). See app/importing.py."""

from __future__ import annotations

from io import BytesIO

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import BooleanField, SelectChoice, SubmitField
from wtforms.validators import NumberRange, Optional

from app.asc_import import NOTES, AscResult, import_asc
from app.auth import admin_required
from app.class_names import current_school_year_start
from app.forms import BgIntegerField, BgSelectField
from app.importing import (
    HEADERS,
    ImportFileError,
    build_workbook,
    import_sheets,
    read_csv,
    read_xlsx,
)

bp = Blueprint("importing", __name__, url_prefix="/data/import")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_SHOWN = 100
ASC_MAX_UPLOAD = (
    32 * 1024 * 1024
)  # a real aSc export is ~20 MB (BE-17); templates stay at 2 MB
YEAR_FROM = {
    "file": "определена от името на файла",
    "codes": "определена от кодовете на класовете",
    "": "въведена от вас",
    "default": "от файла не може да се определи, затова е взета текущата",
}


class ImportForm(FlaskForm):
    file = FileField(
        "Файл (.xlsx или .csv)",
        validators=[
            FileRequired("Изберете файл."),
            FileAllowed(["xlsx", "csv"], "Позволени са само файлове .xlsx и .csv."),
        ],
    )
    sheet = BgSelectField(
        "Към кой лист се отнася CSV файлът?",
        choices=[SelectChoice("", "— (само за CSV)")]
        + [SelectChoice(s, s) for s in HEADERS],
        default="",
    )
    school_year = BgIntegerField(
        "Начало на учебната година (за кодове на класове като 234)",
        default=current_school_year_start,
        validators=[
            Optional(),
            NumberRange(2000, 2099, "Въведете година между 2000 и 2099."),
        ],
    )
    preview = SubmitField("Преглед")
    confirm = BooleanField(
        "Разбирам, че качването на Excel файл замества всички текущи данни."
    )

    @property
    def is_csv(self) -> bool:
        return self.file.data.filename.lower().endswith(".csv")

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        if ok and self.is_csv and not self.sheet.data:
            self.sheet.errors.append("Изберете към кой лист се отнася CSV файлът.")
            ok = False
        if ok and not self.is_csv and not self.confirm.data and not self.preview.data:
            self.confirm.errors.append("Потвърдете замяната на текущите данни.")
            ok = False
        return ok


class AscImportForm(FlaskForm):
    file = FileField(
        "Файл от aSc (.xlsx)",
        validators=[
            FileRequired("Изберете файл."),
            FileAllowed(["xlsx"], "Позволени са само файлове .xlsx."),
        ],
    )
    school_year = BgIntegerField(
        "Начало на учебната година (по избор)",
        validators=[
            Optional(),
            NumberRange(2000, 2099, "Въведете година между 2000 и 2099."),
        ],
    )
    # Hard by default: impossible rules are then named by the solve and fixed with one
    # click ("make them preferences"), which keeps every other rule exact (measured on a
    # real school: 21 broken preferences instead of 105 with everything soft).
    soft_rules = BooleanField("Импортирай правилата като предпочитания")
    preview = SubmitField("Преглед")
    confirm = BooleanField("Разбирам, че импортът замества всички текущи данни.")

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        if ok and not self.confirm.data and not self.preview.data:
            self.confirm.errors.append("Потвърдете замяната на текущите данни.")
            ok = False
        return ok


def _page(form, asc_form, errors=(), conversions=None, previewed=False, asc=None):
    return render_template(
        "data/import.html",
        form=form,
        asc_form=asc_form,
        errors=errors[:MAX_SHOWN],
        hidden=max(0, len(errors) - MAX_SHOWN),
        conversions=conversions or {},
        previewed=previewed,
        asc=asc,
        year_from=YEAR_FROM,
        asc_notes=NOTES,
        headers=HEADERS,
        active="import",
    )


@bp.post("/asc")
@admin_required
def upload_asc():
    request.max_content_length = ASC_MAX_UPLOAD  # before the form reads the body
    form, result = AscImportForm(prefix="asc"), None
    if form.validate_on_submit():
        file = form.file.data
        try:
            result = import_asc(
                file.read(),
                file.filename,
                form.school_year.data,
                form.preview.data,
                hard_rules=not form.soft_rules.data,
            )
        except ImportFileError as e:
            result = AscResult([str(e)])
        if result.year and form.school_year.data is None:
            # Show the year that was used, so the admin can change it before importing.
            form.school_year.raw_data, form.school_year.data = None, result.year
        if not result.errors and not form.preview.data:
            done = ", ".join(f"{sheet}: {n}" for sheet, n in result.counts.items())
            skipped = sum(result.skipped.values())
            flash(f"Данните са импортирани. {done}. Пропуснати: {skipped}.", "success")
            return redirect(url_for("data.teachers_list"))
    errors = result.errors if result else []
    return _page(
        ImportForm(),
        form,
        errors,
        result.conversions if result else None,
        bool(result) and not errors and form.preview.data,
        result,
    )


@bp.route("/", methods=["GET", "POST"])
@admin_required
def upload():
    form, errors, conversions, previewed = ImportForm(), [], {}, False
    if form.validate_on_submit():
        data = form.file.data.read()
        try:
            sheets = read_csv(data, form.sheet.data) if form.is_csv else read_xlsx(data)
            errors, counts, conversions = import_sheets(
                sheets,
                replace=not form.is_csv,
                school_year_start=form.school_year.data,
                dry_run=form.preview.data,
            )
        except ImportFileError as e:
            errors = [str(e)]
        if form.preview.data:
            previewed = not errors
        elif not errors:
            done = ", ".join(f"{sheet}: {n}" for sheet, n in counts.items())
            flash(f"Данните са импортирани. {done}.", "success")
            return redirect(url_for("data.teachers_list"))
    return _page(form, AscImportForm(prefix="asc"), errors, conversions, previewed)


def _download(with_data: bool, name: str):
    buf = BytesIO()
    build_workbook(with_data).save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=name, mimetype=XLSX)


@bp.get("/template.xlsx")
@admin_required
def template():
    return _download(False, "shablon.xlsx")


@bp.get("/export.xlsx")
@admin_required
def export():
    return _download(True, "danni.xlsx")
