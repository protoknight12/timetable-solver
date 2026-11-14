from collections import Counter
from pathlib import Path

import pytest

from solver import solve
from tests.solver.verify import violations
from tools.make_demo_seeds import SCHOOLS
from tools.seed_to_contract import load_seed, seed_to_solver_input

SEEDS = Path(__file__).resolve().parents[2] / "seeds"
FILES = list(SCHOOLS)
SOLVED = ("OPTIMAL", "FEASIBLE")


def seed_of(name: str) -> dict:
    return load_seed(SEEDS / name)


@pytest.mark.parametrize("name", FILES)
def test_file_is_up_to_date(name):
    assert seed_of(name) == SCHOOLS[name]()


@pytest.mark.parametrize("name", FILES)
def test_ids_are_unique_and_references_resolve(name):
    seed = seed_of(name)
    for key in ("teachers", "classes", "subjects", "rooms"):
        counts = Counter(x["id"] for x in seed[key])
        assert all(n == 1 for n in counts.values()), key
    teachers = {t["id"] for t in seed["teachers"]}
    subjects = {s["id"] for s in seed["subjects"]}
    classes = {c["id"] for c in seed["classes"]}
    for c in seed["classes"]:
        assert c["class_teacher"] in teachers
    for item in seed["curriculum"]:
        assert item["teacher"] in teachers
        assert item["subject"] in subjects
        assert all(c["class"] in classes for c in item["classes"])
        assert sum(item["blocks"]) == item["periods_per_week"]


@pytest.mark.parametrize("name", FILES)
def test_is_anonymized_and_valid_for_the_solver(name):
    seed = seed_of(name)
    assert seed["meta"]["anonymized"] is True
    real = {t["name"] for t in seed_of("example_school.json")["teachers"]}
    assert all(t["name"] and t["name"] not in real for t in seed["teachers"])
    assert seed_to_solver_input(seed).problems() == []


def test_sizes_match_the_task():
    tiny, demo = seed_of("tiny_school.json"), seed_of("demo_school.json")
    assert len(tiny["classes"]) == 3
    assert len(demo["classes"]) == 12
    assert len(demo["teachers"]) == 25
    assert len(demo["meta"]["days"]) == 5
    assert len(demo["meta"]["periods"]) == 7


def test_impossible_school_overloads_one_teacher():
    seed = seed_of("impossible_school.json")
    slots = len(seed["meta"]["days"]) * len(seed["meta"]["periods"])
    load = Counter()
    for item in seed["curriculum"]:
        load[item["teacher"]] += item["periods_per_week"]
    assert max(load.values()) > slots


@pytest.mark.parametrize("name", ["tiny_school.json", "demo_school.json"])
def test_solver_finds_a_valid_timetable(name):
    inp = seed_to_solver_input(seed_of(name))
    result = solve(inp, time_limit_s=30, num_workers=2)
    assert result.status in SOLVED
    assert violations(inp, result) == []


def test_solver_reports_the_impossible_school():
    inp = seed_to_solver_input(seed_of("impossible_school.json"))
    assert solve(inp, time_limit_s=30, num_workers=2).status == "INFEASIBLE"
