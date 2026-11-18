"""BE-20: a room must have computers when the subject needs them, and enough seats."""

import pytest

from checker import check
from schema.contract import ContractError, SolverInput, load_solver_input
from solver import _build, run, solve

SOLVED = ("OPTIMAL", "FEASIBLE")


def build(rooms, *, computers=False, needs_room=True, classes=None, groups=None):
    """One class-1 lesson (one block), teacher 1; ``classes``/``groups`` override the defaults."""
    classes = classes or [{"id": 1, "name": "8А"}]
    groups = groups or [{"id": 1, "class_id": 1, "is_whole": True}]
    part = {
        "id": 1,
        "subject_id": 1,
        "group_ids": [groups[0]["id"]],
        "teacher_ids": [1],
        "needs_room": needs_room,
    }
    return {
        "days": [1, 2],
        "periods": [1, 2, 3],
        "teachers": [{"id": 1}],
        "subjects": [{"id": 1, "requires_computers": computers}],
        "classes": classes,
        "groups": groups,
        "rooms": rooms,
        "courses": [
            {"id": 1, "periods_per_week": 1, "block_sizes": [1], "parts": [part]}
        ],
    }


def unvalidated_status(data):
    """The solver's own verdict, skipping the contract check that would raise first."""
    tm = _build(SolverInput.from_dict(data))
    return run(tm, 10, 1, 0).status


def solved_rooms(data):
    inp = load_solver_input(data)
    res = solve(inp, time_limit_s=10, num_workers=1)
    assert res.status in SOLVED
    assert check(inp, res) == []  # the independent checker agrees
    return {x.room_id for x in res.lessons}


def test_computer_subject_lands_in_a_computer_room():
    rooms = [
        {"id": 1, "type": "classroom"},
        {"id": 2, "type": "classroom", "has_computers": True},
    ]
    assert solved_rooms(build(rooms, computers=True)) == {2}


def test_computers_imply_a_room_even_without_needs_room():
    # ASSUMPTION (BE-20): a subject that requires computers needs a room.
    rooms = [
        {"id": 1, "type": "classroom"},
        {"id": 2, "type": "lab", "has_computers": True},
    ]
    assert solved_rooms(build(rooms, computers=True, needs_room=False)) == {2}


def test_plain_subject_may_use_any_room_and_computer_rooms_are_not_forced():
    rooms = [{"id": 1, "type": "classroom", "has_computers": True}]
    assert solved_rooms(build(rooms)) == {1}


def test_no_computer_room_is_reported_and_infeasible():
    rooms = [{"id": 1, "type": "classroom"}, {"id": 2, "type": "lab"}]
    data = build(rooms, computers=True)
    with pytest.raises(ContractError) as e:
        load_solver_input(data)
    assert [p.code for p in e.value.problems] == ["no_matching_room"]
    with pytest.raises(ContractError):
        solve(SolverInput.from_dict(data), time_limit_s=10, num_workers=1)
    # Without the validation the model itself has no solution.
    assert unvalidated_status(data) == "INFEASIBLE"


CLASSES = [
    {"id": 1, "name": "8А", "student_count": 30},
    {"id": 2, "name": "8Б", "student_count": 20},
]


ONE_CLASS = [{"id": 1, "name": "8А", "student_count": 30}]


def two_class_data(rooms, classes=CLASSES, groups=None):
    groups = groups or [
        {"id": 1, "class_id": 1, "is_whole": True},
        {"id": 2, "class_id": 2, "is_whole": True},
    ]
    return build(rooms, classes=classes, groups=groups)


def test_seats_must_fit_the_class():
    rooms = [
        {"id": 1, "type": "classroom", "seats": 25},
        {"id": 2, "type": "classroom", "seats": 30},
    ]
    data = build(rooms, classes=ONE_CLASS)
    assert solved_rooms(data) == {2}  # 30 students: 25 seats are too few


def test_joint_lesson_sums_the_classes():
    rooms = [
        {"id": 1, "type": "classroom", "seats": 49},
        {"id": 2, "type": "classroom", "seats": 50},
        {"id": 3, "type": "classroom"},  # no seat count: no limit
    ]
    data = two_class_data(rooms)
    data["courses"][0]["parts"][0]["group_ids"] = [1, 2]  # 30 + 20 = 50
    assert solved_rooms(data) <= {2, 3} and solved_rooms(data)


def test_sub_group_uses_its_own_count():
    classes = [{"id": 1, "name": "8А", "student_count": 30}]
    groups = [
        {"id": 1, "class_id": 1, "is_whole": True},
        {
            "id": 2,
            "class_id": 1,
            "is_whole": False,
            "split": "групи",
            "student_count": 14,
        },
    ]
    data = build(
        [{"id": 1, "type": "classroom", "seats": 15}], classes=classes, groups=groups
    )
    data["courses"][0]["parts"][0]["group_ids"] = [2]
    assert solved_rooms(data) == {1}  # 14 <= 15, although the class has 30


def test_unknown_count_skips_the_seats_rule():
    # The class count is unknown -> a 5-seat room is fine (ASSUMPTION: unknown = no limit).
    data = build([{"id": 1, "type": "classroom", "seats": 5}])
    assert solved_rooms(data) == {1}
    # A sub-group without a count: also skipped, even if the class count is known.
    classes = [{"id": 1, "name": "8А", "student_count": 30}]
    groups = [
        {"id": 1, "class_id": 1, "is_whole": True},
        {"id": 2, "class_id": 1, "is_whole": False, "split": "групи"},
    ]
    data = build(
        [{"id": 1, "type": "classroom", "seats": 5}], classes=classes, groups=groups
    )
    data["courses"][0]["parts"][0]["group_ids"] = [2]
    assert solved_rooms(data) == {1}


def test_rooms_too_small_for_every_option_are_reported_and_infeasible():
    data = build([{"id": 1, "type": "classroom", "seats": 10}], classes=ONE_CLASS)
    with pytest.raises(ContractError) as e:
        load_solver_input(data)
    assert [p.code for p in e.value.problems] == ["no_matching_room"]
    assert unvalidated_status(data) == "INFEASIBLE"


def test_a_part_without_a_room_ignores_seats():
    data = build(
        [{"id": 1, "type": "classroom", "seats": 1}],
        classes=ONE_CLASS,
        needs_room=False,
    )
    inp = load_solver_input(data)
    res = solve(inp, time_limit_s=10, num_workers=1)
    assert res.status in SOLVED and res.lessons[0].room_id is None
    assert check(inp, res) == []
