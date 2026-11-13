"""Shared data contract. Plain data only: no Flask, SQLAlchemy or app/ imports."""

from schema.constraints import (
    ConstraintModel,
    constraint_json_schema,
    parse_constraint,
)
from schema.contract import (
    CONSTRAINT_TYPES,
    CONTRACT_VERSION,
    SOLVE_STATUSES,
    Constraint,
    ContractError,
    Course,
    CoursePart,
    LessonPlacement,
    Problem,
    Room,
    SchoolClass,
    SolveResult,
    SolverInput,
    StudentGroup,
    Subject,
    Teacher,
    load_solver_input,
)

__all__ = [
    "CONSTRAINT_TYPES",
    "CONTRACT_VERSION",
    "SOLVE_STATUSES",
    "Constraint",
    "ConstraintModel",
    "ContractError",
    "Course",
    "CoursePart",
    "LessonPlacement",
    "Problem",
    "Room",
    "SchoolClass",
    "SolveResult",
    "SolverInput",
    "StudentGroup",
    "Subject",
    "Teacher",
    "constraint_json_schema",
    "load_solver_input",
    "parse_constraint",
]
