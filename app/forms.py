"""Flask-WTF forms for the data entry pages (FE-05a). Messages are Bulgarian."""

from __future__ import annotations

from flask_wtf import FlaskForm
from sqlalchemy import select
from wtforms import (
    IntegerField,
    PasswordField,
    SelectChoice,
    SelectField,
    StringField,
    TimeField,
    ValidationError,
)
from wtforms.validators import (
    DataRequired,
    EqualTo,
    InputRequired,
    Length,
    NumberRange,
    Optional,
    Regexp,
)

from app.models import ROLES, Room, User, db

REQUIRED = "Полето е задължително."


def _strip(value):
    return value.strip() if isinstance(value, str) else value


def _strip_or_none(value):
    return _strip(value) or None


def room_types() -> list[str]:
    return list(db.session.scalars(select(Room.type).distinct().order_by(Room.type)))


def _canonical_type(value):
    """Reuse the spelling of an existing room type so "Lab" and "lab" stay one type."""
    for existing in room_types() if value else ():
        if existing.casefold() == value.casefold():
            return existing
    return value


class _BlankIsNone:
    """A blank input is "no value" (the validators decide); bad text gets a Bulgarian error."""

    bg_error = ""

    def process_formdata(self, valuelist):
        if not valuelist or not valuelist[0].strip():
            self.data = None
            return
        try:
            super().process_formdata(valuelist)
        except ValueError:
            raise ValueError(self.bg_error) from None


class BgIntegerField(_BlankIsNone, IntegerField):
    bg_error = "Въведете цяло число."


class BgTimeField(_BlankIsNone, TimeField):
    bg_error = "Въведете час във формат ЧЧ:ММ."


def _name(label: str, max_len: int, **kw) -> StringField:
    return StringField(
        label,
        filters=[_strip],
        validators=[
            DataRequired(REQUIRED),
            Length(max=max_len, message=_too_long(max_len)),
        ],
        **kw,
    )


def _too_long(max_len: int) -> str:
    return f"Най-много {max_len} знака."


class DataForm(FlaskForm):
    """Keeps the edited row, so uniqueness checks can ignore it."""

    def __init__(self, *args, obj=None, **kwargs):
        self.obj = obj
        super().__init__(*args, obj=obj, **kwargs)


class TeacherForm(DataForm):
    name = _name("Име", 120)


class SubjectForm(DataForm):
    name = _name("Име", 120)
    default_room_type = StringField(
        "Тип стая по подразбиране (по избор)",
        filters=[_strip_or_none, _canonical_type],
        validators=[Optional(), Length(max=40, message=_too_long(40))],
    )


class RoomForm(DataForm):
    name = _name("Име", 80)
    type = StringField(
        "Тип",
        filters=[_strip, _canonical_type],
        validators=[DataRequired(REQUIRED), Length(max=40, message=_too_long(40))],
    )
    concurrent_capacity = BgIntegerField(
        "Едновременно провеждани часове",
        default=1,
        validators=[
            InputRequired(REQUIRED),
            NumberRange(min=1, message="Стойността трябва да е поне 1."),
        ],
    )


class ClassForm(DataForm):
    name = _name("Име (например 10А)", 40)
    grade = BgIntegerField(
        "Година на обучение (1–12, по избор)",
        validators=[
            Optional(),
            NumberRange(1, 12, "Годината на обучение трябва да е между 1 и 12."),
        ],
    )


class GroupForm(DataForm):
    name = _name("Име на групата", 80)
    split = _name("Разделяне (например „групи“)", 40)


class PeriodForm(DataForm):
    start_time = BgTimeField(
        "Начало", format=["%H:%M", "%H:%M:%S"], validators=[InputRequired(REQUIRED)]
    )
    end_time = BgTimeField(
        "Край", format=["%H:%M", "%H:%M:%S"], validators=[InputRequired(REQUIRED)]
    )

    def validate_end_time(self, field):
        start = self.start_time.data
        if start and field.data and field.data <= start:
            raise ValidationError("Краят трябва да е след началото.")


class RegisterForm(DataForm):
    username = StringField(
        "Потребителско име",
        filters=[_strip],
        validators=[
            DataRequired(REQUIRED),
            Length(3, 64, "Между 3 и 64 знака."),
            Regexp(
                r"^[\w.\-]+$",
                message="Само букви, цифри, точка, тире и долна черта.",
            ),
        ],
    )
    password = PasswordField(
        "Парола",
        validators=[DataRequired(REQUIRED), Length(8, 128, "Между 8 и 128 знака.")],
    )
    confirm = PasswordField(
        "Повторете паролата",
        validators=[
            DataRequired(REQUIRED),
            EqualTo("password", "Паролите не съвпадат."),
        ],
    )

    def validate_username(self, field):
        if db.session.scalar(select(User).where(User.username == field.data)):
            raise ValidationError("Потребителското име е заето.")


ROLE_LABELS = {"admin": "Администратор", "viewer": "Преглед"}


class UserCreateForm(RegisterForm):
    role = SelectField(
        "Роля",
        choices=[SelectChoice(r, ROLE_LABELS[r]) for r in ROLES],
        default="viewer",
    )
