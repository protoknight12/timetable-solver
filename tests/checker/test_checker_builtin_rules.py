from checker import check
from tests.checker.helpers import course, lesson, part, school, solved, v

ONE = course(1, [1], part(1))  # 8А, teacher 1, one 1-period block


def test_valid_timetable_passes():
    assert check(school([ONE]), solved(lesson(1, 1, 1, 1))) == []


def test_no_timetable_status():
    assert check(school([ONE]), solved(status="INFEASIBLE")) == [
        v("status", "no_timetable")
    ]


def test_unknown_references():
    bad = solved(
        lesson(1, 1, 1, 1),
        lesson(9, 1, 1, 1),
        lesson(1, 9, 1, 1),
        lesson(1, 1, 2, 1, room=9),
    )
    assert check(school([ONE]), bad) == [
        v("references", "unknown_course", ("course", 9)),
        v("references", "unknown_part", ("course", 1), ("part", 9)),
        v("references", "unknown_room", ("room", 9)),
    ]


def test_missing_block():
    assert check(school([ONE]), solved()) == [
        v("blocks", "wrong_block_count", ("course", 1), ("part", 1))
    ]


def test_block_length_must_match():
    two = course(1, [2], part(1))
    assert check(school([two]), solved(lesson(1, 1, 1, 1, 2))) == []
    assert check(school([two]), solved(lesson(1, 1, 1, 1, 1))) == [
        v("blocks", "wrong_block_length", ("course", 1), ("part", 1))
    ]


def test_block_must_stay_in_the_day():
    two = course(1, [2], part(1))
    assert check(school([two]), solved(lesson(1, 1, 1, 2, 2))) == []
    assert check(school([two]), solved(lesson(1, 1, 1, 3, 2))) == [
        v("blocks", "block_outside_grid", ("course", 1), ("part", 1), ("day", 1))
    ]
    assert check(school([ONE]), solved(lesson(1, 1, 3, 1))) == [
        v("blocks", "block_outside_grid", ("course", 1), ("part", 1), ("day", 3))
    ]


def test_blocks_of_one_course_must_not_overlap():
    two = course(1, [2, 1], part(1))
    # back to back on one day is allowed (TECH_STACK section 5)
    assert check(school([two]), solved(lesson(1, 1, 1, 1, 2), lesson(1, 1, 1, 3))) == []
    # overlapping blocks also double-book the teacher and the class
    got = check(school([two]), solved(lesson(1, 1, 1, 1, 2), lesson(1, 1, 1, 2)))
    assert {x.message_key for x in got} == {
        "blocks_overlap",
        "teacher_double_booked",
        "groups_double_booked",
    }


def test_parts_of_a_course_run_together():
    split = course(1, [1], part(1, [2], [1]), part(2, [3], [2]))
    assert check(school([split]), solved(lesson(1, 1, 1, 1), lesson(1, 2, 1, 1))) == []
    assert check(school([split]), solved(lesson(1, 1, 1, 1), lesson(1, 2, 1, 2))) == [
        v("blocks", "parts_not_simultaneous", ("course", 1))
    ]


def test_teacher_in_one_place_per_slot():
    other_class = course(2, [1], part(2, [5], [1]))
    ok = solved(lesson(1, 1, 1, 1), lesson(2, 2, 1, 2))
    assert check(school([ONE, other_class]), ok) == []
    clash = solved(lesson(1, 1, 1, 1), lesson(2, 2, 1, 1))
    assert check(school([ONE, other_class]), clash) == [
        v(
            "teacher_conflict",
            "teacher_double_booked",
            ("teacher", 1),
            ("day", 1),
            ("period", 1),
        )
    ]


def test_groups_of_the_same_split_may_share_a_slot():
    a, b = course(1, [1], part(1, [2], [1])), course(2, [1], part(2, [3], [2]))
    assert check(school([a, b]), solved(lesson(1, 1, 1, 1), lesson(2, 2, 1, 1))) == []


def test_conflicting_groups_never_share_a_slot():
    def clash(g1, g2):
        a = course(1, [1], part(1, [g1], [1]))
        b = course(2, [1], part(2, [g2], [2]))
        return check(school([a, b]), solved(lesson(1, 1, 1, 1), lesson(2, 2, 1, 1)))

    assert clash(1, 5) == []  # different classes
    for g1, g2 in [(1, 2), (2, 1), (2, 2), (2, 4)]:  # whole vs group, same, other split
        assert clash(g1, g2) == [
            v(
                "group_conflict",
                "groups_double_booked",
                ("group", g1),
                ("group", g2),
                ("day", 1),
                ("period", 1),
            )
        ]


def test_room_capacity():
    def rooms_at(room_a, room_b):
        a = course(1, [1], part(1, [1], [1], needs_room=True))
        b = course(2, [1], part(2, [5], [2], needs_room=True))
        s = solved(lesson(1, 1, 1, 1, room=room_a), lesson(2, 2, 1, 1, room=room_b))
        return check(school([a, b]), s)

    assert rooms_at(1, 2) == []
    assert rooms_at(3, 3) == []  # the gym holds two
    assert rooms_at(1, 1) == [
        v("room_capacity", "room_over_capacity", ("room", 1), ("day", 1), ("period", 1))
    ]


def test_room_must_match_type_and_allowed_rooms():
    lab = course(1, [1], part(1, needs_room=True, room_type="lab"))
    assert check(school([lab]), solved(lesson(1, 1, 1, 1, room=1))) == []
    assert check(school([lab]), solved(lesson(1, 1, 1, 1, room=2))) == [
        v("room_match", "room_mismatch", ("course", 1), ("part", 1), ("room", 2))
    ]
    assert check(school([lab]), solved(lesson(1, 1, 1, 1))) == [
        v("room_match", "room_missing", ("course", 1), ("part", 1))
    ]
    only = course(1, [1], part(1, needs_room=True, allowed_room_ids=(2,)))
    assert check(school([only]), solved(lesson(1, 1, 1, 1, room=2))) == []
    assert check(school([only]), solved(lesson(1, 1, 1, 1, room=3))) == [
        v("room_match", "room_mismatch", ("course", 1), ("part", 1), ("room", 3))
    ]
