"""Tests for the Pydantic constraint models in schema/constraints.py."""

import json
import typing
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from schema import CONSTRAINT_TYPES, SolverInput, parse_constraint
from schema.constraints import SCHEMA_FILE, ConstraintModel, _render_schema

EXAMPLE = (
    Path(__file__).resolve().parents[2] / "schema" / "examples" / "split_practice.json"
)

VALID = {
    "teacher_available_days": {"teacher_id": 2, "days": [2, 4]},
    "teacher_unavailable_slots": {
        "teacher_id": 1,
        "slots": [{"day": 1, "period": 1}, {"day": 1, "period": 2}],
    },
    "room_type_required": {"subject_id": 3, "room_type": "lab"},
    "max_lessons_per_day": {"scope": {"class_id": 1}, "max": 6},
    "no_gaps": {"scope": "all"},
    "fixed_slot": {"course_id": 3, "blocks": [{"day": 5, "start_period": 1}]},
}

INVALID = [
    ("teacher_available_days", {"days": [1]}),  # missing key
    ("teacher_available_days", {"teacher_id": 1, "days": [1], "x": 1}),  # extra key
    ("teacher_available_days", {"teacher_id": "1", "days": [1]}),  # string id
    ("teacher_available_days", {"teacher_id": 1, "days": []}),  # empty list
    ("teacher_available_days", {"teacher_id": 1, "days": [2, 2]}),  # duplicates
    ("teacher_available_days", {"teacher_id": 1, "days": [0]}),  # day < 1
    ("teacher_available_days", {"teacher_id": True, "days": [1]}),  # bool is not int
    ("teacher_unavailable_slots", {"teacher_id": 1, "slots": []}),
    ("teacher_unavailable_slots", {"teacher_id": 1, "slots": [{"day": 1}]}),
    (
        "teacher_unavailable_slots",
        {"teacher_id": 1, "slots": [{"day": 1, "period": 1}] * 2},
    ),
    (
        "teacher_unavailable_slots",
        {"teacher_id": 1, "slots": [{"day": 1, "period": 1, "room": 2}]},
    ),
    ("room_type_required", {"room_type": "lab"}),  # neither id
    ("room_type_required", {"subject_id": 1, "course_id": 1, "room_type": "lab"}),
    ("room_type_required", {"subject_id": 1, "room_type": ""}),
    ("room_type_required", {"subject_id": 1}),
    ("max_lessons_per_day", {"scope": {"class_id": 1}, "max": 0}),
    ("max_lessons_per_day", {"scope": {"class_id": 1}}),
    ("max_lessons_per_day", {"scope": "all", "max": 6}),  # "all" only for no_gaps
    ("max_lessons_per_day", {"scope": {}, "max": 6}),
    ("max_lessons_per_day", {"scope": {"class_id": 1, "grade": 9}, "max": 6}),
    ("no_gaps", {"scope": "everyone"}),
    ("no_gaps", {"scope": {"room_id": 1}}),
    ("no_gaps", {}),
    ("fixed_slot", {"course_id": 3, "blocks": []}),
    ("fixed_slot", {"course_id": 3, "blocks": [{"day": 5}]}),
    ("fixed_slot", {"course_id": 3, "blocks": [{"day": 5, "start_period": "1"}]}),
]


def con(type_, params, **envelope):
    return {"type": type_, "hard": True, "params": params, **envelope}


def example():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_example_constraints_parse():
    for c in example()["constraints"]:
        envelope = {k: v for k, v in c.items() if k != "id"}
        assert (
            parse_constraint(envelope).to_solver(c["id"])
            == SolverInput.from_dict({**example(), "constraints": [c]}).constraints[0]
        )


@pytest.mark.parametrize("type_", VALID)
def test_valid(type_):
    assert parse_constraint(con(type_, VALID[type_])).type == type_


@pytest.mark.parametrize(("type_", "params"), INVALID)
def test_invalid(type_, params):
    with pytest.raises(ValidationError):
        parse_constraint(con(type_, params))


def test_room_type_required_with_course_id():
    c = parse_constraint(
        con("room_type_required", {"course_id": 3, "room_type": "lab"})
    )
    assert c.to_solver(1).params == {"course_id": 3, "room_type": "lab"}


@pytest.mark.parametrize(
    ("hard", "weight", "ok"),
    [(True, None, True), (True, 5, False), (False, 5, True), (False, None, False)]
    + [(False, 0, False), (False, "5", False)],
)
def test_weight_rules(hard, weight, ok):
    data = {"type": "no_gaps", "hard": hard, "params": {"scope": "all"}}
    if weight is not None:
        data["weight"] = weight
    if ok:
        parse_constraint(data)
    else:
        with pytest.raises(ValidationError):
            parse_constraint(data)


@pytest.mark.parametrize(
    "data",
    [
        con("teacher_free_day", {"teacher_id": 1}),  # unknown type
        {"hard": True, "params": {"scope": "all"}},  # no type
        con("no_gaps", {"scope": "all"}, id=1),  # id belongs to the DB, not here
        {"type": "no_gaps", "hard": 1, "params": {"scope": "all"}},  # 1 is not true
    ],
)
def test_bad_envelope(data):
    with pytest.raises(ValidationError):
        parse_constraint(data)


def test_types_match_contract():
    members = typing.get_args(typing.get_args(ConstraintModel)[0])
    names = {typing.get_args(m.model_fields["type"].annotation)[0] for m in members}
    assert names == set(CONSTRAINT_TYPES)


def test_to_solver_output_passes_contract_checks():
    inp = SolverInput.from_dict(example())
    params = {**VALID, "max_lessons_per_day": {"scope": {"grade": 9}, "max": 6}}
    solver = tuple(
        parse_constraint(con(t, p)).to_solver(i)
        for i, (t, p) in enumerate(params.items(), 100)
    )
    assert replace(inp, constraints=inp.constraints + solver).problems() == []


def test_committed_schema_is_fresh():
    assert SCHEMA_FILE.read_text(encoding="utf-8") == _render_schema(), (
        "run: uv run python -m schema"
    )
