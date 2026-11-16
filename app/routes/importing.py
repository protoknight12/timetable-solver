"""FE-13: import school data from an Excel/CSV template (admin only). See app/importing.py."""

from __future__ import annotations

from io import BytesIO

from flask import Blueprint, flash, redirect, render_template, send_file, url_for
from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import BooleanField, SelectChoice, SubmitField
from wtforms.validators import NumberRange, Optional

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
    return render_template(
        "data/import.html",
        form=form,
        errors=errors[:MAX_SHOWN],
        hidden=max(0, len(errors) - MAX_SHOWN),
        conversions=conversions,
        previewed=previewed,
        headers=HEADERS,
        active="import",
    )


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
