"""Integrity checks for the anonymized seed in seeds/example_school.json.

These do not check timetable rules (that is the checker's job). They make sure
the seed is internally consistent, so solver/checker tests can rely on it.
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "seeds" / "example_school.json"
sys.path.insert(0, str(ROOT / "tools" / "asc_import"))
from anonymize import anonymize


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


def test_seed_hides_the_real_school(seed):
    # TECH_STACK §15 q12: no real room numbers or room codes, generic bell times.
    assert seed["meta"]["anonymized"] is True
    for room in seed["rooms"]:
        assert re.fullmatch(r"[RLG]\d\d", room["id"]), room
        assert re.fullmatch(r"(Стая|Лаборатория|Физкултурен салон) \d+", room["name"])
    assert seed["meta"]["periods"][0]["start"] == "7:30"
    text = json.dumps(seed, ensure_ascii=False)
    assert "СБ" not in text and "STEM" not in text


def test_anonymize_keeps_structure_and_is_idempotent():
    raw = {
        "meta": {"periods": [{"number": n} for n in (1, 2, 3, 4)], "known_quirks": []},
        "rooms": [
            {"id": "101", "name": "101", "type": "regular"},
            {"id": "СБ", "name": "СБ", "type": "gym"},
        ],
        "reference_timetable": [
            {"id": "A1", "day": 0, "start_period": 1, "length": 2, "room": "СБ",
             "teacher": "T1", "subject": "S1"},
            {"id": "A2", "day": 4, "start_period": 3, "length": 1, "room": None,
             "teacher": "T1", "subject": "S2"},
        ],
    }  # fmt: skip
    once = anonymize(raw)
    assert [r["id"] for r in once["rooms"]] == ["R01", "G01"]
    acts = once["reference_timetable"]
    assert sorted((a["day"], a["length"], a["room"]) for a in acts) == [
        (1, 1, None),
        (2, 2, "G01"),
    ]
    assert [p["start"] for p in once["meta"]["periods"]] == [
        "7:30",
        "8:25",
        "9:20",
        "10:25",
    ]
    assert anonymize(once) == once
