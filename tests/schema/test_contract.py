"""Tests for the plain-data contract in schema/contract.py."""

import copy
import json
from pathlib import Path

import pytest

from schema import (
    ContractError,
    LessonPlacement,
    SolveResult,
    SolverInput,
    load_solver_input,
)

EXAMPLE = (
    Path(__file__).resolve().parents[2] / "schema" / "examples" / "split_practice.json"
)


@pytest.fixture
def data():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def codes(d):
    """Problem codes for a dict that parses but may be inconsistent."""
    return {p.code for p in SolverInput.from_dict(d).problems()}


def course(d, course_id):
    return next(c for c in d["courses"] if c["id"] == course_id)


# --- valid data -------------------------------------------------------------


def test_example_is_valid(data):
    inp = load_solver_input(data)
    assert len(inp.courses) == 5
    split = next(c for c in inp.courses if c.id == 3)
    assert [p.group_ids for p in split.parts] == [(2,), (3,)]


def test_round_trip_through_json(data):
    inp = load_solver_input(data)
    again = load_solver_input(json.loads(json.dumps(inp.to_dict())))
    assert again == inp


def test_defaults_are_filled(data):
    inp = load_solver_input(data)
    assert next(r for r in inp.rooms if r.id == 1).concurrent_capacity == 1
    part = inp.courses[0].parts[0]
    assert part.needs_room is False and part.room_type is None


def test_parallel_parts_with_disjoint_split_groups_are_fine(data):
    # Група 1 and Група 2 of the same class may run at once (OS vs DB practice).
    assert codes(data) == set()


# --- shape errors (from_dict) -----------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("courses"),
        lambda d: d.update(extra=1),
        lambda d: d["teachers"][0].update(id="T1"),
        lambda d: d["teachers"][0].update(id=True),
        lambda d: d["groups"][0].update(is_whole="yes"),
        lambda d: d["courses"][0]["parts"][0].update(group_ids=1),
        lambda d: d["courses"][0]["parts"][0].update(room="1"),
        lambda d: d["constraints"][0].update(params=[]),
        lambda d: d.update(contract_version=2),
    ],
    ids=[
        "missing_key",
        "unknown_key",
        "string_id",
        "bool_id",
        "non_bool_flag",
        "non_list_ids",
        "typo_in_part_key",
        "params_not_object",
        "wrong_version",
    ],
)
def test_bad_shape_is_rejected(data, mutate):
    mutate(data)
    with pytest.raises(ContractError) as err:
        SolverInput.from_dict(data)
    assert err.value.problems[0].code == "bad_shape"


# --- consistency errors (problems) ------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda d: d.update(periods=[1, 2, 4]), "bad_time_grid"),
        (lambda d: d.update(days=[1, 1, 2]), "bad_time_grid"),
        (lambda d: d["teachers"].append({"id": 1}), "duplicate_id"),
        (lambda d: course(d, 2)["parts"][0].update(id=1), "duplicate_id"),
        (lambda d: d["groups"].pop(0), "whole_group_count"),
        (
            lambda d: d["groups"].append({"id": 9, "class_id": 2, "is_whole": True}),
            "whole_group_count",
        ),
        (lambda d: d["groups"][1].update(class_id=99), "unknown_reference"),
        (lambda d: d["rooms"][3].update(concurrent_capacity=0), "bad_capacity"),
        (lambda d: course(d, 1).update(block_sizes=[2, 2]), "bad_blocks"),
        (lambda d: course(d, 1).update(block_sizes=[]), "bad_blocks"),
        (lambda d: course(d, 1).update(block_sizes=[0, 3]), "bad_blocks"),
        (
            lambda d: course(d, 1).update(periods_per_week=6, block_sizes=[1] * 6),
            "bad_blocks",
        ),
        (
            lambda d: course(d, 3).update(periods_per_week=8, block_sizes=[8]),
            "bad_blocks",
        ),
        (lambda d: course(d, 1).update(parts=[]), "empty_course"),
        (lambda d: course(d, 1)["parts"][0].update(group_ids=[]), "empty_part"),
        (lambda d: course(d, 1)["parts"][0].update(teacher_ids=[]), "empty_part"),
        (lambda d: course(d, 1)["parts"][0].update(subject_id=99), "unknown_reference"),
        (
            lambda d: course(d, 1)["parts"][0].update(group_ids=[99]),
            "unknown_reference",
        ),
        (
            lambda d: course(d, 1)["parts"][0].update(teacher_ids=[99]),
            "unknown_reference",
        ),
        (lambda d: course(d, 1)["parts"][0].update(teacher_ids=[1, 1]), "duplicate_id"),
        # Parts of one course run at once: whole class next to Група 1 is impossible.
        (
            lambda d: course(d, 3)["parts"][1].update(group_ids=[1]),
            "conflicting_groups",
        ),
        (
            lambda d: course(d, 3)["parts"][1].update(group_ids=[2]),
            "conflicting_groups",
        ),
        (
            lambda d: course(d, 1)["parts"][0].update(group_ids=[1, 2]),
            "conflicting_groups",
        ),
        (lambda d: course(d, 3)["parts"][1].update(teacher_ids=[3]), "shared_teacher"),
        (
            lambda d: course(d, 1)["parts"][0].update(room_type="lab"),
            "room_without_needs_room",
        ),
        (
            lambda d: course(d, 3)["parts"][0].update(room_type="pool"),
            "no_matching_room",
        ),
        (
            lambda d: course(d, 4)["parts"][0].update(room_type="gym"),
            "no_matching_room",
        ),
        (
            lambda d: course(d, 4)["parts"][0].update(allowed_room_ids=[42]),
            "unknown_reference",
        ),
    ],
)
def test_inconsistent_input_is_reported(data, mutate, expected):
    mutate(data)
    assert expected in codes(data)
    with pytest.raises(ContractError):
        load_solver_input(data)


# --- constraints --------------------------------------------------------------


def constraint(d, constraint_id):
    return next(c for c in d["constraints"] if c["id"] == constraint_id)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda d: constraint(d, 3).update(type="teacher_likes_tea"),
            "unknown_constraint_type",
        ),
        (lambda d: constraint(d, 3).update(weight=3), "bad_weight"),
        (lambda d: constraint(d, 4).pop("weight"), "bad_weight"),
        (lambda d: constraint(d, 4).update(weight=0), "bad_weight"),
        # The teacher, course or class a constraint points at was deleted.
        (
            lambda d: constraint(d, 3)["params"].update(teacher_id=99),
            "unknown_reference",
        ),
        (
            lambda d: constraint(d, 1)["params"].update(course_id=99),
            "unknown_reference",
        ),
        (
            lambda d: constraint(d, 4)["params"].update(scope={"class_id": 99}),
            "unknown_reference",
        ),
        (
            lambda d: constraint(d, 4)["params"].update(scope={"grade": 12}),
            "unknown_reference",
        ),
        (
            lambda d: d["constraints"].append(
                {
                    "id": 9,
                    "type": "room_type_required",
                    "hard": True,
                    "params": {"subject_id": 99, "room_type": "lab"},
                }
            ),
            "unknown_reference",
        ),
        (
            lambda d: d["constraints"].append(
                {
                    "id": 9,
                    "type": "room_type_required",
                    "hard": True,
                    "params": {"subject_id": 3, "room_type": "pool"},
                }
            ),
            "unknown_reference",
        ),
        (lambda d: constraint(d, 3)["params"].update(days=[6]), "out_of_range"),
        (lambda d: constraint(d, 1)["params"].update(day=7), "out_of_range"),
        (lambda d: constraint(d, 1)["params"].update(start_period=12), "out_of_range"),
        (
            lambda d: d["constraints"].append(
                {
                    "id": 9,
                    "type": "teacher_unavailable_slots",
                    "hard": True,
                    "params": {"teacher_id": 1, "slots": [{"day": 1, "period": 9}]},
                }
            ),
            "out_of_range",
        ),
    ],
)
def test_bad_constraints_are_reported(data, mutate, expected):
    mutate(data)
    assert expected in codes(data)


def test_all_problems_are_reported_at_once(data):
    constraint(data, 3)["params"]["teacher_id"] = 99
    course(data, 1)["parts"][0]["subject_id"] = 99
    with pytest.raises(ContractError) as err:
        load_solver_input(data)
    assert len(err.value.problems) == 2


def test_input_is_not_mutated(data):
    before = copy.deepcopy(data)
    load_solver_input(data)
    assert data == before


# --- result -------------------------------------------------------------------


def test_result_round_trip():
    raw = {
        "status": "FEASIBLE",
        "lessons": [
            {
                "course_id": 3,
                "part_id": 3,
                "day": 5,
                "start_period": 1,
                "length": 2,
                "room_id": 2,
            },
            {
                "course_id": 3,
                "part_id": 4,
                "day": 5,
                "start_period": 1,
                "length": 2,
                "room_id": 3,
            },
            {"course_id": 1, "part_id": 1, "day": 2, "start_period": 3, "length": 1},
        ],
        "objective": 12,
        "solve_time_s": 8,
    }
    result = SolveResult.from_dict(raw)
    assert result.lessons[2] == LessonPlacement(1, 1, 2, 3, 1, None)
    assert result.solve_time_s == 8.0
    assert SolveResult.from_dict(json.loads(json.dumps(result.to_dict()))) == result


def test_infeasible_result_names_constraints():
    result = SolveResult.from_dict(
        {"status": "INFEASIBLE", "conflicting_constraint_ids": [3, 4]}
    )
    assert result.lessons == ()
    assert result.conflicting_constraint_ids == (3, 4)


@pytest.mark.parametrize(
    "raw",
    [
        {"status": "DONE"},
        {"status": "FEASIBLE", "solve_time_s": "8"},
        {"status": "FEASIBLE", "lessons": [{"course_id": 1}]},
        {"status": "FEASIBLE", "objective": 1.5},
    ],
)
def test_bad_result_is_rejected(raw):
    with pytest.raises(ContractError):
        SolveResult.from_dict(raw)
