"""BE-20: contract fields for computers, seats and student counts."""

import json
from pathlib import Path

import pytest

from schema.contract import ContractError, SolverInput, load_solver_input

EXAMPLE = (
    Path(__file__).resolve().parents[2] / "schema" / "examples" / "split_practice.json"
)


@pytest.fixture
def data():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def codes(data):
    return {p.code for p in SolverInput.from_dict(data).problems()}


def test_old_input_without_the_new_keys_still_loads(data):
    inp = load_solver_input(data)
    assert all(not r.has_computers and r.seats is None for r in inp.rooms)
    assert all(not s.requires_computers for s in inp.subjects)
    assert all(k.student_count is None for k in inp.classes)
    assert all(g.student_count is None for g in inp.groups)


def test_new_fields_round_trip(data):
    for room in data["rooms"]:
        room["has_computers"] = True
    data["rooms"][0]["seats"] = 24
    data["subjects"][0]["requires_computers"] = True
    data["classes"][0]["student_count"] = 26
    data["groups"][0]["student_count"] = 13
    inp = load_solver_input(data)
    assert (inp.rooms[0].has_computers, inp.rooms[0].seats) == (True, 24)
    assert inp.subjects[0].requires_computers and inp.classes[0].student_count == 26
    assert inp.groups[0].student_count == 13
    assert load_solver_input(json.loads(json.dumps(inp.to_dict()))) == inp


@pytest.mark.parametrize(
    "where, key",
    [("rooms", "seats"), ("classes", "student_count"), ("groups", "student_count")],
)
def test_counts_must_be_at_least_one(data, where, key):
    data[where][0][key] = 0
    assert "bad_count" in codes(data)


def test_wrong_types_are_rejected(data):
    data["rooms"][0]["has_computers"] = "yes"
    with pytest.raises(ContractError):
        SolverInput.from_dict(data)
