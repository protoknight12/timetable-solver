"""Typed constraint models (Pydantic v2). Single source of truth for the format.

Every constraint type is a Pydantic model with a fixed ``type`` literal and
typed ``params``. The JSON Schema given to the LLM is generated from these
models (``constraint_json_schema``), never written by hand.

What lives here vs. ``schema/contract.py``:
- here: the *shape* of one constraint (required keys, value types, hard/soft
  and weight rules). Used for manual form input and for LLM output.
- contract.py: whether a constraint fits a given school (the teacher exists,
  the day is a school day, the block fits). That needs the whole SolverInput.

Like contract.py, this module must never import Flask, SQLAlchemy or app/.

Regenerate the committed schema file after any change here:

    uv run python -m schema
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from schema import contract

SCHEMA_FILE = Path(__file__).with_name("constraint.schema.json")

# Positive id or number, e.g. a teacher id, a day or a period.
PosInt = Annotated[int, Field(ge=1)]


class _Model(BaseModel):
    # strict: "12" is not accepted as 12, so LLM mistakes are rejected, not guessed.
    # extra="forbid": unknown keys are an error, not silently dropped.
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------


class ClassScope(_Model):
    class_id: PosInt = Field(description="Id of one school class.")


class GradeScope(_Model):
    grade: PosInt = Field(description="Applies to every class in this grade, e.g. 9.")


class Slot(_Model):
    day: PosInt = Field(description="School day number, 1 = Monday.")
    period: PosInt = Field(
        description="Period number within the day, 1 = first period."
    )


class BlockPosition(_Model):
    day: PosInt = Field(description="School day number, 1 = Monday.")
    start_period: PosInt = Field(description="Period the block starts in.")


def _unique(values: list, what: str) -> list:
    if len(set(values)) != len(values):
        raise ValueError(f"{what} must not repeat")
    return values


# --------------------------------------------------------------------------
# Params per type
# --------------------------------------------------------------------------


TeacherId = Annotated[PosInt, Field(description="Id of the teacher.")]
CourseId = Annotated[PosInt, Field(description="Id of one course.")]


class TeacherAvailableDaysParams(_Model):
    teacher_id: TeacherId
    days: list[PosInt] = Field(
        min_length=1,
        json_schema_extra={"uniqueItems": True},
        description="The only days the teacher can teach, 1 = Monday.",
    )

    @model_validator(mode="after")
    def _check(self) -> TeacherAvailableDaysParams:
        _unique(self.days, "days")
        return self


class TeacherUnavailableSlotsParams(_Model):
    teacher_id: TeacherId
    slots: list[Slot] = Field(
        min_length=1,
        json_schema_extra={"uniqueItems": True},
        description="Slots the teacher cannot teach in.",
    )

    @model_validator(mode="after")
    def _check(self) -> TeacherUnavailableSlotsParams:
        _unique([(s.day, s.period) for s in self.slots], "slots")
        return self


class RoomTypeRequiredParams(_Model):
    subject_id: PosInt | None = Field(
        default=None,
        description="Every lesson of this subject. Give this or course_id.",
    )
    course_id: PosInt | None = Field(
        default=None, description="Only this one course. Give this or subject_id."
    )
    room_type: str = Field(min_length=1, description="Room type, e.g. 'lab' or 'gym'.")

    @model_validator(mode="after")
    def _check(self) -> RoomTypeRequiredParams:
        if (self.subject_id is None) == (self.course_id is None):
            raise ValueError("give exactly one of subject_id and course_id")
        return self


class MaxLessonsPerDayParams(_Model):
    scope: ClassScope | GradeScope
    max: PosInt = Field(description="Most periods the class may have in one day.")


class NoGapsParams(_Model):
    scope: ClassScope | GradeScope | Literal["all"] = Field(
        description="One class, one grade, or 'all' classes."
    )


class FixedSlotParams(_Model):
    course_id: CourseId
    blocks: list[BlockPosition] = Field(
        min_length=1,
        description="Where each block of the course goes, one entry per block, "
        "in the same order as the course's block sizes.",
    )


# --------------------------------------------------------------------------
# Constraints
# --------------------------------------------------------------------------


class _Constraint(_Model):
    hard: bool = Field(description="true = must always hold; false = a preference.")
    weight: PosInt | None = Field(
        default=None,
        description="Only for preferences (hard = false): how much it matters, 1 or more.",
    )

    @model_validator(mode="after")
    def _check_weight(self) -> _Constraint:
        if self.hard and self.weight is not None:
            raise ValueError("hard constraints have no weight")
        if not self.hard and self.weight is None:
            raise ValueError("soft constraints need a weight")
        return self

    def to_solver(self, constraint_id: int) -> contract.Constraint:
        """Plain contract object for the solver; ``constraint_id`` is the DB id."""
        return contract.Constraint(
            id=constraint_id,
            type=self.type,  # type: ignore[attr-defined]
            hard=self.hard,
            weight=self.weight,
            params=self.params.model_dump(mode="json", exclude_none=True),  # type: ignore[attr-defined]
        )


class TeacherAvailableDays(_Constraint):
    """The teacher works only on the listed days."""

    type: Literal["teacher_available_days"]
    params: TeacherAvailableDaysParams


class TeacherUnavailableSlots(_Constraint):
    """The teacher cannot teach in the listed slots."""

    type: Literal["teacher_unavailable_slots"]
    params: TeacherUnavailableSlotsParams


class RoomTypeRequired(_Constraint):
    """A subject (or one course) must be held in a room of the given type."""

    type: Literal["room_type_required"]
    params: RoomTypeRequiredParams


class MaxLessonsPerDay(_Constraint):
    """A class (or every class in a grade) has at most N periods per day."""

    type: Literal["max_lessons_per_day"]
    params: MaxLessonsPerDayParams


class NoGaps(_Constraint):
    """No empty periods between two lessons in a student's day."""

    type: Literal["no_gaps"]
    params: NoGapsParams


class FixedSlot(_Constraint):
    """Pins every block of a course to a given day and start period."""

    type: Literal["fixed_slot"]
    params: FixedSlotParams


ConstraintModel = Annotated[
    TeacherAvailableDays
    | TeacherUnavailableSlots
    | RoomTypeRequired
    | MaxLessonsPerDay
    | NoGaps
    | FixedSlot,
    Field(discriminator="type"),
]

_ADAPTER: TypeAdapter[Any] = TypeAdapter(ConstraintModel)


def parse_constraint(data: Any) -> _Constraint:
    """Validate one constraint dict. Raises ``pydantic.ValidationError``."""
    return _ADAPTER.validate_python(data)


def constraint_json_schema() -> dict[str, Any]:
    """JSON Schema for one constraint, generated from the models above."""
    return _ADAPTER.json_schema()


def _render_schema() -> str:
    return json.dumps(constraint_json_schema(), indent=2, ensure_ascii=False) + "\n"
