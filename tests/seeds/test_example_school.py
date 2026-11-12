"""Integrity checks for the anonymized seed in seeds/example_school.json.

These do not check timetable rules (that is the checker's job). They make sure
the seed is internally consistent, so solver/checker tests can rely on it.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

SEED = Path(__file__).resolve().parents[2] / "seeds" / "example_school.json"


@pytest.fixture(scope="module")
def seed():
    return json.loads(SEED.read_text(encoding="utf-8"))


def ids(items):
    return {x["id"] for x in items}


def test_ids_are_unique(seed):
    for key in ("teachers", "classes", "subjects", "rooms", "reference_timetable"):
        counts = Counter(x["id"] for x in seed[key])
        assert not [i for i, n in counts.items() if n > 1], key


def test_references_resolve(seed):
    teachers, classes = ids(seed["teachers"]), ids(seed["classes"])
    subjects, rooms = ids(seed["subjects"]), ids(seed["rooms"])
    for c in seed["classes"]:
        assert c["class_teacher"] in teachers
    for item in seed["curriculum"] + seed["reference_timetable"]:
        assert item["teacher"] in teachers
        assert item["subject"] in subjects
        assert item["classes"]
        assert all(c["class"] in classes for c in item["classes"])
    for a in seed["reference_timetable"]:
        assert a["room"] is None or a["room"] in rooms


def test_lessons_fit_in_the_day(seed):
    periods = {p["number"] for p in seed["meta"]["periods"]}
    days = len(seed["meta"]["days"])
    for a in seed["reference_timetable"]:
        assert 0 <= a["day"] < days
        assert a["length"] >= 1
        assert set(range(a["start_period"], a["start_period"] + a["length"])) <= periods


def test_curriculum_matches_reference_timetable(seed):
    from_curriculum = sum(c["periods_per_week"] for c in seed["curriculum"])
    from_timetable = sum(a["length"] for a in seed["reference_timetable"])
    assert from_curriculum == from_timetable
    for c in seed["curriculum"]:
        assert sum(c["blocks"]) == c["periods_per_week"]
