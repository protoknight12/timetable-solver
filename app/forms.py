"""Flask-WTF forms for the data entry pages (FE-05a). Messages are Bulgarian."""

from __future__ import annotations

import re

from flask_wtf import FlaskForm
from sqlalchemy import func, select
from wtforms import (
    BooleanField,
    IntegerField,
    PasswordField,
    SelectChoice,
    SelectField,
    SelectMultipleField,
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
from wtforms.widgets import ColorInput

from app.class_names import NAME_ERROR, NAME_RE, normalize_class_name
from app.models import (
    ROLES,
    Period,
    Room,
    SchoolClass,
    StudentGroup,
    Subject,
    Teacher,
    User,
    db,
)
from app.room_types import room_type_choices, room_type_label
from app.subject_colors import PALETTE, is_color, subject_color

REQUIRED = "Полето е задължително."
MIN_ONE = "Стойността трябва да е поне 1."


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


class _BgSelectMixin:
    """Bulgarian messages instead of WTForms' English defaults."""

    def __init__(self, *args, message="Невалиден избор.", **kwargs):
        super().__init__(
            *args,
            invalid_choice_message=message,
            invalid_value_message=message,
            **kwargs,
        )


class BgSelectField(_BgSelectMixin, SelectField):
    pass


class BgSelectMultipleField(_BgSelectMixin, SelectMultipleField):
    pass


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
    short_name = StringField(
        "Кратко име (по избор, например И. Петров)",
        filters=[_strip_or_none],
        validators=[Optional(), Length(max=40, message=_too_long(40))],
    )
    # "" = no login. Only viewers not yet linked to another teacher are offered.
    account = SelectField("Профил за вход", default="")

    def __init__(self, *args, obj=None, **kwargs):
        super().__init__(*args, obj=obj, **kwargs)
        free = User.teacher_id.is_(None)
        if obj is not None:
            free |= User.teacher_id == obj.id
        users = db.session.scalars(
            select(User).where(User.role == "viewer", free).order_by(User.username)
        )
        self.account.choices = [("", "Без профил")] + [
            (str(u.id), u.username) for u in users
        ]
        if obj is not None and not self.is_submitted():
            linked = db.session.scalar(select(User.id).where(User.teacher_id == obj.id))
            self.account.data = str(linked or "")

    def populate_obj(self, obj):
        obj.name = self.name.data
        obj.short_name = self.short_name.data  # `account`: see _link_account


class SubjectForm(DataForm):
    name = _name("Име", 120)
    default_room_type = BgSelectField(
        "Тип стая по подразбиране",
        coerce=lambda v: v or None,
        choices=room_type_choices("Без изискване"),
        default="",
        message="Изберете тип стая от списъка.",
    )
    short_name = StringField(
        "Кратко име (по избор)",
        filters=[_strip_or_none],
        validators=[Optional(), Length(max=20, message=_too_long(20))],
    )
    requires_computers = BooleanField("Изисква компютри")
    color = StringField("Цвят", widget=ColorInput(), filters=[_strip])
    auto_color = BooleanField("Автоматичен цвят")

    def __init__(self, *args, obj=None, **kwargs):
        super().__init__(*args, obj=obj, **kwargs)
        if not self.is_submitted():
            # The picker starts at the color the grid shows now.
            self.auto_color.data = obj is None or obj.color is None
            self.color.data = subject_color(obj) if obj else PALETTE[0]

    def _auto(self) -> bool:
        return bool(self.auto_color.data) or not self.color.data  # blank = import file

    def validate_color(self, field):
        # Trust boundary: the value ends up inside CSS. Ignored while "automatic" is on.
        if not self._auto() and not is_color(field.data):
            raise ValidationError("Изберете цвят във формат #rrggbb.")

    def populate_obj(self, obj):
        obj.name = self.name.data
        obj.default_room_type = self.default_room_type.data
        obj.short_name = self.short_name.data
        obj.requires_computers = bool(self.requires_computers.data)
        obj.color = None if self._auto() else self.color.data.lower()


class RoomForm(DataForm):
    name = _name("Име", 80)
    type = BgSelectField(
        "Тип",
        choices=room_type_choices(),
        validators=[DataRequired(REQUIRED)],
        message="Изберете тип стая от списъка.",
    )
    concurrent_capacity = BgIntegerField(
        "Едновременно провеждани часове",
        default=1,
        validators=[
            InputRequired(REQUIRED),
            NumberRange(min=1, message="Стойността трябва да е поне 1."),
        ],
    )
    seats = BgIntegerField(
        "Места (по избор)",
        validators=[Optional(), NumberRange(min=1, message=MIN_ONE)],
    )
    has_computers = BooleanField("Има компютри")


class ClassForm(DataForm):
    name = StringField(
        "Име (например 10А)",
        filters=[normalize_class_name],
        validators=[
            DataRequired(REQUIRED),
            Regexp(NAME_RE.pattern + "$", message=NAME_ERROR),
        ],
    )
    grade = BgIntegerField(
        "Година на обучение (1–12, по избор)",
        validators=[
            Optional(),
            NumberRange(1, 12, "Годината на обучение трябва да е между 1 и 12."),
        ],
    )
    student_count = BgIntegerField(
        "Брой ученици (по избор)",
        validators=[Optional(), NumberRange(min=1, message=MIN_ONE)],
    )
    # "" = none. Plain SelectField, so form.html styles it as a select.
    class_teacher_id = SelectField(
        "Класен ръководител (по избор)",
        coerce=lambda v: int(v) if v else None,
        default="",
    )

    def __init__(self, *args, obj=None, **kwargs):
        super().__init__(*args, obj=obj, **kwargs)
        teachers = db.session.scalars(
            select(Teacher).order_by(Teacher.name, Teacher.id)
        )
        self.class_teacher_id.choices = [("", "Без класен ръководител")] + [
            (t.id, t.name) for t in teachers
        ]

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        if ok:
            from_name = int(NAME_RE.fullmatch(self.name.data).group(1))
            if self.grade.data is None:
                self.grade.data = from_name
            elif self.grade.data != from_name:
                self.grade.errors.append(
                    "Годината на обучение не съвпада с името на класа."
                )
                ok = False
        return ok


class GroupForm(DataForm):
    name = _name("Име на групата", 80)
    split = _name("Разделяне (например „групи“)", 40)
    student_count = BgIntegerField(
        "Брой ученици (по избор)",
        validators=[Optional(), NumberRange(min=1, message=MIN_ONE)],
    )


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


class BlockSizesField(StringField):
    """ "2, 1" <-> [2, 1]: the sizes of the blocks a course is split into."""

    bg_error = "Въведете положителни цели числа, разделени със запетая (например 2, 1)."

    def _value(self):
        if isinstance(self.data, list):
            return ", ".join(str(n) for n in self.data)
        return self.raw_data[0] if self.raw_data else ""

    def process_formdata(self, valuelist):
        text = valuelist[0].strip() if valuelist else ""
        if not text:
            self.data = None
            return
        try:
            sizes = [int(t) for t in re.split(r"[\s,;+]+", text) if t]
        except ValueError:
            sizes = []
        if not sizes or min(sizes) < 1:
            self.data = None
            raise ValueError(self.bg_error)
        self.data = sizes


class CourseForm(DataForm):
    label = StringField(
        "Име (по избор, например „Химия 10А“)",
        filters=[_strip],
        validators=[Optional(), Length(max=120, message=_too_long(120))],
    )
    periods_per_week = BgIntegerField(
        "Учебни часове седмично",
        validators=[
            InputRequired(REQUIRED),
            NumberRange(min=1, message="Стойността трябва да е поне 1."),
        ],
    )
    block_sizes = BlockSizesField(
        "Блокове (например „2, 1“: един двучасов и един едночасов)",
        validators=[InputRequired(REQUIRED)],
    )
    week = SelectField(
        "Седмица",
        choices=[("", "всяка"), ("A", "само A"), ("B", "само B")],
        coerce=lambda v: v or "",
        validate_choice=False,  # a missing field (old clients, imports) means every week
    )

    def validate_week(self, field):
        if field.data not in (None, "", "A", "B"):
            raise ValidationError("Невалиден избор.")

    def populate_obj(self, obj):
        super().populate_obj(obj)
        obj.week = obj.week or None  # "" and None both mean every week

    def validate_block_sizes(self, field):
        sizes, per_week = field.data, self.periods_per_week.data
        if sizes is None or per_week is None:
            return
        if sum(sizes) != per_week:
            raise ValidationError(
                f"Сборът на блоковете ({sum(sizes)}) трябва да е равен "
                f"на часовете седмично ({per_week})."
            )
        day = db.session.scalar(select(func.count()).select_from(Period))
        if day and max(sizes) > day:
            raise ValidationError(
                f"Блок не може да е по-дълъг от учебния ден ({day} часа)."
            )


def group_label(group: StudentGroup) -> str:
    return f"{group.school_class.name} — {group.name}"


def _groups_conflict(a: StudentGroup, b: StudentGroup) -> bool:
    # The rule of TECH_STACK §5 (groups of one class that may share students),
    # restated here to give the admin an error at entry time. The solver input
    # check (SolverInput.problems) still reports it if it ever slips through.
    if a.class_id != b.class_id:
        return False
    return a.is_whole or b.is_whole or a.id == b.id or a.split != b.split


class PartForm(DataForm):
    """One part of a course: what a set of groups does during the course's blocks.

    Not a ``populate_obj`` form: the route copies ids to rows itself. Pass the
    initial values with ``data=`` (see ``part_initial``).
    """

    subject = BgSelectField(
        "Предмет", coerce=int, choices=[], message="Изберете предмет."
    )
    groups = BgSelectMultipleField(
        "Групи",
        coerce=int,
        choices=[],
        validators=[DataRequired("Изберете поне една група.")],
        message="Изберете групи от списъка.",
    )
    teachers = BgSelectMultipleField(
        "Учители",
        coerce=int,
        choices=[],
        validators=[DataRequired("Изберете поне един учител.")],
        message="Изберете учители от списъка.",
    )
    needs_room = BooleanField("Изисква стая")
    room_type = BgSelectField(
        "Тип стая",
        coerce=lambda v: v or None,
        choices=room_type_choices("Без изискване"),
        default="",
        message="Изберете тип стая от списъка.",
    )
    allowed_room_ids = BgSelectMultipleField(
        "Позволени стаи (по избор)",
        coerce=int,
        choices=[],
        message="Изберете стаи от списъка.",
    )

    def __init__(self, *args, course, part=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.course, self.part = course, part
        scalars = db.session.scalars
        self.subject.choices = [
            (s.id, s.name)
            for s in scalars(select(Subject).order_by(Subject.name, Subject.id))
        ]
        self.teachers.choices = [
            (t.id, t.name)
            for t in scalars(select(Teacher).order_by(Teacher.name, Teacher.id))
        ]
        groups = scalars(
            select(StudentGroup)
            .join(SchoolClass)
            .order_by(
                SchoolClass.grade,
                SchoolClass.name,
                SchoolClass.id,
                StudentGroup.is_whole.desc(),
                StudentGroup.id,
            )
        )
        self.groups.choices = [(g.id, group_label(g)) for g in groups]
        self.allowed_room_ids.choices = [
            (r.id, f"{r.name} ({room_type_label(r.type)})")
            for r in scalars(select(Room).order_by(Room.name, Room.id))
        ]

    def _other_parts(self):
        return [p for p in self.course.parts if not self.part or p.id != self.part.id]

    def validate_teachers(self, field):
        # All parts of a course run at the same time (TECH_STACK §5).
        busy = {t.id: t.name for p in self._other_parts() for t in p.teachers}
        clash = [busy[t] for t in field.data or () if t in busy]
        if clash:
            raise ValidationError(
                f"Вече е в друга част на този учебен план: {', '.join(clash)}. "
                "Частите се провеждат едновременно."
            )

    def validate_groups(self, field):
        chosen = list(
            db.session.scalars(
                select(StudentGroup).where(StudentGroup.id.in_(field.data or ()))
            )
        )
        others = [g for p in self._other_parts() for g in p.groups]
        for i, a in enumerate(chosen):
            for b in [*chosen[:i], *others]:
                if _groups_conflict(a, b):
                    raise ValidationError(
                        f"„{group_label(a)}“ и „{group_label(b)}“ имат общи ученици "
                        "и не могат да са в един учебен план."
                    )

    def validate(self, extra_validators=None):
        # Not an inline validator: those are skipped while room_type is blank.
        ok = super().validate(extra_validators)
        if ok and not self._room_matches():
            self.room_type.errors.append(
                "Няма стая, която да отговаря на избраните тип, позволени стаи и изискване за компютри."
            )
            return False
        return ok

    def _room_matches(self) -> bool:
        subject = db.session.get(Subject, self.subject.data)
        computers = bool(subject and subject.requires_computers)
        if not (self.needs_room.data or computers):
            return True
        # Same fallback as build_solver_input: the subject's default room type.
        # Without needs_room the room fields are cleared on save, so only the
        # subject's default type counts (the subject needs computers, so a room anyway).
        chosen = self.room_type.data if self.needs_room.data else None
        wanted = chosen or (subject.default_room_type if subject else None)
        allowed = set(self.allowed_room_ids.data or ()) if self.needs_room.data else ()
        return any(
            (wanted is None or r.type == wanted)
            and (not allowed or r.id in allowed)
            and (not computers or r.has_computers)
            for r in db.session.scalars(select(Room))
        )


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
