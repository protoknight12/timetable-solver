from checker import check
from tests.checker.helpers import con, course, lesson, part, school, solved, v

ONE = course(1, [1], part(1))  # 8А, teacher 1, subject 1


def run(constraint, courses, *lessons):
    return check(school(courses, [constraint]), solved(*lessons))


def test_teacher_available_days():
    c = con(7, "teacher_available_days", {"teacher_id": 1, "days": [2]})
    assert run(c, [ONE], lesson(1, 1, 2, 1)) == []
    assert run(c, [ONE], lesson(1, 1, 1, 1)) == [
        v("teacher_available_days", "teacher_unavailable_day",
          ("teacher", 1), ("course", 1), ("day", 1), cid=7)
    ]  # fmt: skip


def test_teacher_unavailable_slots():
    c = con(
        7,
        "teacher_unavailable_slots",
        {"teacher_id": 1, "slots": [{"day": 1, "period": 1}]},
    )
    assert run(c, [ONE], lesson(1, 1, 1, 2)) == []
    assert run(c, [ONE], lesson(1, 1, 1, 1)) == [
        v("teacher_unavailable_slots", "teacher_unavailable_slot",
          ("teacher", 1), ("course", 1), ("day", 1), ("period", 1), cid=7)
    ]  # fmt: skip


def test_room_type_required_for_subject_and_course():
    gym_part = course(1, [1], part(1, needs_room=True))
    for params in (
        {"subject_id": 1, "room_type": "lab"},
        {"course_id": 1, "room_type": "lab"},
    ):
        c = con(7, "room_type_required", params)
        assert run(c, [gym_part], lesson(1, 1, 1, 1, room=1)) == []
        assert run(c, [gym_part], lesson(1, 1, 1, 1, room=2)) == [
            v(
                "room_type_required",
                "room_type_not_met",
                ("course", 1),
                ("part", 1),
                cid=7,
            )
        ]


def test_max_lessons_per_day():
    c = con(7, "max_lessons_per_day", {"scope": {"class_id": 1}, "max": 1})
    two = [course(1, [1], part(1)), course(2, [1], part(2, teachers=(2,)))]
    assert run(c, two, lesson(1, 1, 1, 1), lesson(2, 2, 2, 1)) == []
    assert run(c, two, lesson(1, 1, 1, 1), lesson(2, 2, 1, 2)) == [
        v("max_lessons_per_day", "too_many_lessons", ("class", 1), ("day", 1), cid=7)
    ]


def test_max_lessons_counts_parallel_split_lessons_once():
    c = con(7, "max_lessons_per_day", {"scope": {"grade": 8}, "max": 1})
    split = [course(1, [1], part(1, [2], [1])), course(2, [1], part(2, [3], [2]))]
    assert run(c, split, lesson(1, 1, 1, 1), lesson(2, 2, 1, 1)) == []


def test_no_gaps():
    two = [course(1, [1], part(1)), course(2, [1], part(2, teachers=(2,)))]
    for scope in ("all", {"class_id": 1}, {"grade": 8}):
        c = con(7, "no_gaps", {"scope": scope})
        assert run(c, two, lesson(1, 1, 1, 1), lesson(2, 2, 1, 2)) == []
        assert run(c, two, lesson(1, 1, 1, 1), lesson(2, 2, 1, 3)) == [
            v("no_gaps", "gap_in_day", ("class", 1), ("day", 1), cid=7)
        ]


def test_fixed_slot():
    c = con(
        7, "fixed_slot", {"course_id": 1, "blocks": [{"day": 2, "start_period": 1}]}
    )
    assert run(c, [ONE], lesson(1, 1, 2, 1)) == []
    assert run(c, [ONE], lesson(1, 1, 1, 1)) == [
        v(
            "fixed_slot",
            "block_not_at_fixed_slot",
            ("course", 1),
            ("part", 1),
            ("block", 0),
            cid=7,
        )
    ]


def test_fixed_slot_follows_block_order():
    two = course(1, [2, 1], part(1))
    blocks = [{"day": 1, "start_period": 1}, {"day": 2, "start_period": 1}]
    c = con(7, "fixed_slot", {"course_id": 1, "blocks": blocks})
    assert run(c, [two], lesson(1, 1, 1, 1, 2), lesson(1, 1, 2, 1)) == []
    assert run(c, [two], lesson(1, 1, 1, 1, 2), lesson(1, 1, 2, 2)) == [
        v(
            "fixed_slot",
            "block_not_at_fixed_slot",
            ("course", 1),
            ("part", 1),
            ("block", 1),
            cid=7,
        )
    ]


def test_soft_constraint_gives_a_soft_violation():
    c = con(
        7,
        "teacher_available_days",
        {"teacher_id": 1, "days": [2]},
        hard=False,
        weight=3,
    )
    got = run(c, [ONE], lesson(1, 1, 1, 1))
    assert [(x.hard, x.constraint_id) for x in got] == [(False, 7)]


def test_unknown_constraint_type_is_reported():
    c = con(9, "max_same_subject_per_day", {})
    assert run(c, [ONE], lesson(1, 1, 1, 1)) == [
        v("unknown_constraint_type", "unknown_constraint_type", cid=9)
    ]


def test_malformed_params_are_reported():
    c = con(9, "teacher_available_days", {"teacher_id": 1})
    assert run(c, [ONE], lesson(1, 1, 1, 1)) == [
        v("teacher_available_days", "invalid_constraint_params", cid=9)
    ]


def split_run(constraint, a_periods, b_periods):
    """Groups 2 and 3 (one split) of class 1, one 1-period course per period."""
    cs, ls = [], []
    for cid, gid, ps in ((1, 2, a_periods), (2, 3, b_periods)):
        cs.append(course(cid, [1] * len(ps), part(cid, [gid], [cid])))
        ls += [lesson(cid, cid, 1, p) for p in ps]
    return check(school(cs, [constraint], periods=(1, 2, 3, 4)), solved(*ls))


def test_max_lessons_is_counted_per_student_combination():
    c = con(7, "max_lessons_per_day", {"scope": {"class_id": 1}, "max": 2})
    assert split_run(c, [1, 2], [3, 4]) == []  # 4 in the class, 2 for each student
    assert split_run(c, [1, 2, 3], [4]) == [
        v("max_lessons_per_day", "too_many_lessons", ("class", 1), ("day", 1), cid=7)
    ]


def test_no_gaps_is_counted_per_student_combination():
    c = con(7, "no_gaps", {"scope": {"class_id": 1}})
    # the class as a whole is busy in 1-4, but group 2's students have a gap
    assert split_run(c, [1, 4], [2, 3]) == [
        v("no_gaps", "gap_in_day", ("class", 1), ("day", 1), cid=7)
    ]
    assert split_run(c, [1, 2], [3, 4]) == []
    assert split_run(c, [2, 3], [1, 4]) == [  # free periods at the edges are no gap
        v("no_gaps", "gap_in_day", ("class", 1), ("day", 1), cid=7)
    ]
    assert split_run(c, [2, 3], [2, 3]) == []
