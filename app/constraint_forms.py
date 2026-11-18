"""One form per constraint type (FE-06). The shape rules live in schema/constraints.py.

Each form builds the ``params`` dict and the whole constraint is then validated by
``parse_constraint`` plus the contract's reference check, so the admin gets an
error at entry time instead of a failed solve.
"""

from __future__ import annotations

import dataclasses
import re

from flask import current_app
from pydantic import ValidationError as PydanticError
from sqlalchemy import select
from wtforms import BooleanField, StringField
from wtforms.validators import (
    DataRequired,
    InputRequired,
    NumberRange,
    Optional,
)

from app.forms import (
    REQUIRED,
    BgIntegerField,
    BgSelectField,
    BgSelectMultipleField,
    DataForm,
)
from app.models import Course, Period, SchoolClass, Subject, Teacher, db
from app.room_types import room_type_choices
from app.solver_input import build_solver_input
from schema.constraints import parse_constraint

DAY_NAMES = {
    1: "понеделник",
    2: "вторник",
    3: "сряда",
    4: "четвъртък",
    5: "петък",
    6: "събота",
    7: "неделя",
}


class PairsField(StringField):
    """ "1:2, 3:1" <-> [(1, 2), (3, 1)]: pairs of day and period."""

    bg_error = "Въведете двойки „ден:час“, разделени със запетая (например 1:2, 3:1)."

    def _value(self):
        if isinstance(self.data, list):
            return ", ".join(f"{a}:{b}" for a, b in self.data)
        return self.raw_data[0] if self.raw_data else ""

    def process_formdata(self, valuelist):
        text = valuelist[0].strip() if valuelist else ""
        if not text:
            self.data = None
            return
        try:
            pairs = [
                tuple(int(x) for x in t.split(":"))
                for t in re.split(r"[\s,;]+", text)
                if t
            ]
        except ValueError:
            pairs = []
        if not pairs or any(len(p) != 2 for p in pairs):
            self.data = None
            raise ValueError(self.bg_error)
        self.data = pairs


def _course_name(course: Course) -> str:
    return course.label or f"учебен план №{course.id}"


def _choices(model, label, *order):
    return [
        (r.id, label(r)) for r in db.session.scalars(select(model).order_by(*order))
    ]


class ConstraintForm(DataForm):
    kind = ""  # the constraint type
    label = ""  # Bulgarian name, shown in menus, headings and the list

    hard = BooleanField("Твърдо ограничение (винаги се спазва)", default=True)
    weight = BgIntegerField(
        "Тежест (само за предпочитания, 1 или повече)",
        validators=[
            Optional(),
            NumberRange(min=1, message="Стойността трябва да е поне 1."),
        ],
    )

    problem = ""  # form-level error
    # Optional prebuilt SolverInput: the multi-target modal (FE-20) validates one
    # form per target and must not rebuild the whole input each time.
    solver_input = None

    def params(self) -> dict:
        raise NotImplementedError

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        # Not an inline validator: Optional() would skip it while weight is blank.
        if ok and not self.hard.data and self.weight.data is None:
            self.weight.errors.append("Задайте тежест на предпочитанието.")
            ok = False
        if ok:
            ok = self._check_whole()
        return ok

    def constraint_data(self) -> dict:
        return {
            "type": self.kind,
            "hard": self.hard.data,
            "weight": None if self.hard.data else self.weight.data,
            "params": self.params(),
        }

    def _check_whole(self) -> bool:
        self.problem = constraint_problem(self.constraint_data(), self.solver_input)[1]
        return not self.problem


def constraint_problem(data: dict, inp=None):
    """Validate one constraint dict: ``(parsed, None)`` or ``(None, Bulgarian problem)``.

    Shared by the manual form and the LLM proposals (BE-09): confirming a saved
    row later does not validate again, so nothing invalid may be saved.
    """
    try:
        parsed = parse_constraint(data)
    except PydanticError:
        return None, "Ограничението не е валидно. Проверете въведените стойности."
    inp = inp or build_solver_input(db.session)
    inp = dataclasses.replace(inp, constraints=(parsed.to_solver(0),))
    bad = [p.code for p in inp.problems() if p.path.startswith("constraints[0]")]
    if bad:
        return None, (
            "Ограничението не съответства на данните на училището "
            f"(код: {', '.join(sorted(set(bad)))})."
        )
    return parsed, None


def _teacher_field():
    return BgSelectField("Учител", coerce=int, choices=[], message="Изберете учител.")


def _scope_field(label="Обхват"):
    return BgSelectField(label, choices=[], message="Изберете обхват.")


def _scope_choices(with_all: bool) -> list[tuple[str, str]]:
    classes = db.session.scalars(
        select(SchoolClass).order_by(
            SchoolClass.grade, SchoolClass.name, SchoolClass.id
        )
    ).all()
    choices = [(f"class:{c.id}", f"Клас {c.name}") for c in classes]
    grades = sorted({c.grade for c in classes if c.grade})
    choices += [(f"grade:{g}", f"Всички класове в {g}. клас") for g in grades]
    return ([("all", "Всички класове")] if with_all else []) + choices


def _scope(value: str):
    if value == "all":
        return "all"
    key, _, number = value.partition(":")
    return {"class_id" if key == "class" else "grade": int(number)}


class TeacherAvailableDaysForm(ConstraintForm):
    kind, label = "teacher_available_days", "Учител: работни дни"
    teacher = _teacher_field()
    days = BgSelectMultipleField(
        "Дни, в които учителят работи",
        coerce=int,
        choices=[],
        validators=[DataRequired("Изберете поне един ден.")],
        message="Изберете дни от списъка.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.teacher.choices = _choices(
            Teacher, lambda t: t.name, Teacher.name, Teacher.id
        )
        school_days = current_app.config["SCHOOL_DAYS"]
        self.days.choices = [(d, DAY_NAMES[d]) for d in range(1, school_days + 1)]

    def params(self):
        return {"teacher_id": self.teacher.data, "days": sorted(self.days.data)}


class TeacherUnavailableSlotsForm(ConstraintForm):
    kind, label = "teacher_unavailable_slots", "Учител: недостъпни часове"
    teacher = _teacher_field()
    slots = PairsField(
        "Недостъпни часове („ден:час“, напр. 1:2, 3:1; 1 = понеделник)",
        validators=[InputRequired(REQUIRED)],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.teacher.choices = _choices(
            Teacher, lambda t: t.name, Teacher.name, Teacher.id
        )

    def params(self):
        return {
            "teacher_id": self.teacher.data,
            "slots": [{"day": d, "period": p} for d, p in self.slots.data],
        }


class ClassUnavailableSlotsForm(ConstraintForm):
    kind, label = "class_unavailable_slots", "Клас: часове без занятия"
    class_id = BgSelectField("Клас", coerce=int, choices=[], message="Изберете клас.")
    slots = PairsField(
        "Часове без занятия („ден:час“, напр. 1:2, 3:1; 1 = понеделник)",
        validators=[InputRequired(REQUIRED)],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.class_id.choices = _choices(
            SchoolClass, lambda c: f"Клас {c.name}", SchoolClass.grade, SchoolClass.name
        )

    def params(self):
        return {
            "class_id": self.class_id.data,
            "slots": [{"day": d, "period": p} for d, p in self.slots.data],
        }


class SubjectUnavailableSlotsForm(ConstraintForm):
    kind, label = "subject_unavailable_slots", "Предмет: часове без занятия"
    subject_id = BgSelectField(
        "Предмет", coerce=int, choices=[], message="Изберете предмет."
    )
    slots = PairsField(
        "Часове без този предмет („ден:час“, напр. 1:2, 3:1; 1 = понеделник)",
        validators=[InputRequired(REQUIRED)],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.subject_id.choices = _choices(
            Subject, lambda s: s.name, Subject.name, Subject.id
        )

    def params(self):
        return {
            "subject_id": self.subject_id.data,
            "slots": [{"day": d, "period": p} for d, p in self.slots.data],
        }


class RoomTypeRequiredForm(ConstraintForm):
    kind, label = "room_type_required", "Изискван тип стая"
    subject_id = BgSelectField(
        "Предмет (всички негови часове)", coerce=int, choices=[], default=0
    )
    course_id = BgSelectField(
        "Или само един учебен план", coerce=int, choices=[], default=0
    )
    room_type = BgSelectField(
        "Тип стая",
        choices=room_type_choices(blank="—"),
        default="",
        validators=[DataRequired(REQUIRED)],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        blank = [(0, "—")]
        self.subject_id.choices = blank + _choices(
            Subject, lambda s: s.name, Subject.name, Subject.id
        )
        self.course_id.choices = blank + _choices(Course, _course_name, Course.id)

    def params(self):
        out = {"room_type": self.room_type.data}
        if self.subject_id.data:
            out["subject_id"] = self.subject_id.data
        if self.course_id.data:
            out["course_id"] = self.course_id.data
        return out


class MaxLessonsPerDayForm(ConstraintForm):
    kind, label = "max_lessons_per_day", "Най-много часове на ден"
    scope = _scope_field()
    max = BgIntegerField(
        "Най-много часове на ден",
        validators=[
            InputRequired(REQUIRED),
            NumberRange(min=1, message="Стойността трябва да е поне 1."),
        ],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope.choices = _scope_choices(with_all=False)

    def params(self):
        return {"scope": _scope(self.scope.data), "max": self.max.data}


_MAX_RULE = [
    InputRequired(REQUIRED),
    NumberRange(min=1, message="Стойността трябва да е поне 1."),
]


class MaxSameSubjectPerDayForm(ConstraintForm):
    kind, label = "max_same_subject_per_day", "Най-много часове по един предмет на ден"
    scope = _scope_field()
    subject_id = BgSelectField(
        "Предмет", coerce=int, choices=[], message="Изберете предмет."
    )
    max = BgIntegerField("Най-много часове по предмета на ден", validators=_MAX_RULE)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope.choices = _scope_choices(with_all=False)
        self.subject_id.choices = _choices(
            Subject, lambda s: s.name, Subject.name, Subject.id
        )

    def params(self):
        return {
            "scope": _scope(self.scope.data),
            "subject_id": self.subject_id.data,
            "max": self.max.data,
        }


class TeacherMaxLessonsPerDayForm(ConstraintForm):
    kind, label = "teacher_max_lessons_per_day", "Учител: най-много часове на ден"
    teacher = _teacher_field()
    max = BgIntegerField("Най-много часове на ден", validators=_MAX_RULE)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.teacher.choices = _choices(
            Teacher, lambda t: t.name, Teacher.name, Teacher.id
        )

    def params(self):
        return {"teacher_id": self.teacher.data, "max": self.max.data}


class TeacherMaxGapsForm(ConstraintForm):
    kind, label = "teacher_max_gaps", "Учител: най-много прозорци на ден"
    teacher = _teacher_field()
    max = BgIntegerField(
        "Най-много прозорци на ден (0 = без прозорци)",
        validators=[
            InputRequired(REQUIRED),
            NumberRange(min=0, message="Стойността не може да е отрицателна."),
        ],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.teacher.choices = _choices(
            Teacher, lambda t: t.name, Teacher.name, Teacher.id
        )

    def params(self):
        return {"teacher_id": self.teacher.data, "max": self.max.data}


class TeacherMinLessonsPerDayForm(ConstraintForm):
    kind, label = "teacher_min_lessons_per_day", "Учител: най-малко часове на ден"
    teacher = _teacher_field()
    min = BgIntegerField(
        "Най-малко часове в ден, в който преподава", validators=_MAX_RULE
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.teacher.choices = _choices(
            Teacher, lambda t: t.name, Teacher.name, Teacher.id
        )

    def params(self):
        return {"teacher_id": self.teacher.data, "min": self.min.data}


class NoGapsForm(ConstraintForm):
    kind, label = "no_gaps", "Без прозорци"
    scope = _scope_field()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope.choices = _scope_choices(with_all=True)

    def params(self):
        return {"scope": _scope(self.scope.data)}


class DayStartsFirstPeriodForm(ConstraintForm):
    kind, label = "day_starts_first_period", "Денят започва от първия час"
    scope = _scope_field()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope.choices = _scope_choices(with_all=True)

    def params(self):
        return {"scope": _scope(self.scope.data)}


class PeriodsReservedForSubjectsForm(ConstraintForm):
    kind, label = "periods_reserved_for_subjects", "Часове само за определени предмети"
    periods = BgSelectMultipleField(
        "Часове (всеки учебен ден)",
        coerce=int,
        choices=[],
        validators=[DataRequired("Изберете поне един час.")],
        message="Изберете часове от списъка.",
    )
    subject_ids = BgSelectMultipleField(
        "Единствените предмети в тези часове",
        coerce=int,
        choices=[],
        validators=[DataRequired("Изберете поне един предмет.")],
        message="Изберете предмети от списъка.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.periods.choices = [
            (p.number, f"{p.number}. час")
            for p in db.session.scalars(select(Period).order_by(Period.number))
        ]
        self.subject_ids.choices = _choices(
            Subject, lambda s: s.name, Subject.name, Subject.id
        )

    def params(self):
        return {
            "periods": sorted(self.periods.data),
            "subject_ids": sorted(self.subject_ids.data),
        }


class FixedSlotForm(ConstraintForm):
    kind, label = "fixed_slot", "Фиксиран час"
    course_id = BgSelectField(
        "Учебен план", coerce=int, choices=[], message="Изберете учебен план."
    )
    blocks = PairsField(
        "Позиция на всеки блок („ден:начален час“, по един за блок, по реда на блоковете; напр. 1:2, 3:1)",
        validators=[InputRequired(REQUIRED)],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.course_id.choices = _choices(Course, _course_name, Course.id)

    def params(self):
        return {
            "course_id": self.course_id.data,
            "blocks": [{"day": d, "start_period": p} for d, p in self.blocks.data],
        }


@dataclasses.dataclass(frozen=True)
class Target:
    """The form field that names what a constraint applies to (FE-20 multi-picker)."""

    field: str
    title: str  # plural heading of the picker, Bulgarian
    empty: str  # error when nothing is picked


_TEACHERS = ("teacher", "Учители", "Изберете поне един учител.")
_CLASSES = ("class_id", "Класове", "Изберете поне един клас.")
_SCOPES = ("scope", "Класове и групи класове", "Изберете поне един обхват.")

# Types not listed (periods_reserved_for_subjects is global; room_type_required has
# two alternative targets) get no picker and create exactly one constraint.
TARGETS = {
    kind: Target(*t)
    for kind, t in {
        "teacher_available_days": _TEACHERS,
        "teacher_unavailable_slots": _TEACHERS,
        "teacher_max_lessons_per_day": _TEACHERS,
        "teacher_max_gaps": _TEACHERS,
        "teacher_min_lessons_per_day": _TEACHERS,
        "class_unavailable_slots": _CLASSES,
        "subject_unavailable_slots": (
            "subject_id",
            "Предмети",
            "Изберете поне един предмет.",
        ),
        "max_lessons_per_day": _SCOPES,
        "max_same_subject_per_day": _SCOPES,
        "no_gaps": _SCOPES,
        "day_starts_first_period": _SCOPES,
        "fixed_slot": (
            "course_id",
            "Учебни планове",
            "Изберете поне един учебен план.",
        ),
    }.items()
}


FORMS = {
    f.kind: f
    for f in (
        TeacherAvailableDaysForm,
        TeacherUnavailableSlotsForm,
        RoomTypeRequiredForm,
        MaxLessonsPerDayForm,
        NoGapsForm,
        FixedSlotForm,
        MaxSameSubjectPerDayForm,
        TeacherMaxLessonsPerDayForm,
        DayStartsFirstPeriodForm,
        PeriodsReservedForSubjectsForm,
        ClassUnavailableSlotsForm,
        SubjectUnavailableSlotsForm,
        TeacherMaxGapsForm,
        TeacherMinLessonsPerDayForm,
    )
}
