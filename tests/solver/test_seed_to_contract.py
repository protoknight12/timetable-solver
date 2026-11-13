from schema.contract import Course, CoursePart, Room, StudentGroup
from tools.seed_to_contract import load_seed, seed_to_solver_input, split_of

TINY = {
    "meta": {"days": ["Пон", "Вт"], "periods": [{"number": 1}, {"number": 2}]},
    "teachers": [{"id": "T01"}, {"id": "T02"}],
    "subjects": [{"id": "S01"}, {"id": "S02"}],
    "classes": [{"id": "C01", "name": "8А", "grade": 8}],
    "rooms": [
        {"id": "101", "type": "regular"},
        {"id": "STEM1", "type": "lab"},
        {"id": "СБ", "type": "gym"},
    ],
    "curriculum": [
        {
            "classes": [{"class": "C01", "group": None}],
            "subject": "S01",
            "teacher": "T02",
            "periods_per_week": 2,
            "blocks": [1, 1],
        },
        {
            "classes": [
                {"class": "C01", "group": "Първа група"},
                {"class": "C01", "group": "Втора група"},
            ],
            "subject": "S02",
            "teacher": "T01",
            "periods_per_week": 2,
            "blocks": [2],
        },
    ],
    "reference_timetable": [
        # item 1: one lab and one regular placement -> no room requirement
        {"teacher": "T02", "subject": "S01", "room": "STEM1", "day": 0,
         "start_period": 1, "length": 1, "classes": [{"class": "C01", "group": None}]},
        {"teacher": "T02", "subject": "S01", "room": "101", "day": 1,
         "start_period": 1, "length": 1, "classes": [{"class": "C01", "group": None}]},
        # item 2: always in the lab -> needs a lab
        {"teacher": "T01", "subject": "S02", "room": "STEM1", "day": 0,
         "start_period": 1, "length": 2, "classes": [
             {"class": "C01", "group": "Втора група"},
             {"class": "C01", "group": "Първа група"}]},
    ],
}  # fmt: skip


def test_tiny_seed_converts():
    inp = seed_to_solver_input(TINY)
    assert inp.problems() == []
    assert inp.days == (1, 2) and inp.periods == (1, 2)
    assert inp.classes[0].grade == 8
    assert inp.groups == (
        StudentGroup(1, 1, True, "цял клас"),
        StudentGroup(2, 1, False, "Първа група", "групи"),
        StudentGroup(3, 1, False, "Втора група", "групи"),
    )
    assert inp.rooms == (Room(1, "regular"), Room(2, "lab"), Room(3, "gym", 3))
    assert inp.courses == (
        Course(1, 2, (1, 1), (CoursePart(1, 1, (1,), (2,)),)),
        Course(2, 2, (2,), (CoursePart(2, 2, (2, 3), (1,), True, "lab"),)),
    )


def test_real_seed_problems_are_documented():
    inp = seed_to_solver_input(load_seed())
    assert len(inp.courses) == 493
    # Includes the 8th-grade intensive English courses with 6-10 blocks, valid
    # since blocks may share a day (TECH_STACK §15 q10).
    assert max(len(c.block_sizes) for c in inp.courses) == 10
    assert inp.problems() == []


def test_split_names():
    assert split_of("Първа група") == split_of("Втора група") == "групи"
    assert split_of("Китайски") == split_of("Немски") == split_of("Испански") == "езици"


def test_real_seed_has_classes_with_two_splits():
    # 8А-8Г have Група 1/2 and a language group: the reason for group splits.
    inp = seed_to_solver_input(load_seed())
    splits = {}
    for g in inp.groups:
        if not g.is_whole:
            splits.setdefault(g.class_id, set()).add(g.split)
    assert sum(1 for s in splits.values() if s == {"групи", "езици"}) == 11
