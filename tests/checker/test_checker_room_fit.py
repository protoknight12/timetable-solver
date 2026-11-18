"""BE-20: the checker's own room-fit rules (computers, seats). Hand-made results."""

from dataclasses import replace

from checker import check
from schema.contract import Room, SchoolClass, StudentGroup, Subject
from tests.checker.helpers import course, lesson, part, school, solved, v

# Rooms: 1 plain with 20 seats, 2 with computers and 30 seats, 3 with computers, no seat count.
ROOMS = (
    Room(1, "regular", seats=20),
    Room(2, "lab", has_computers=True, seats=30),
    Room(3, "lab", has_computers=True),
)


def fit_school(courses, *, computers_subject=True, counts=(25, 10)):
    inp = school(courses)
    return replace(
        inp,
        rooms=ROOMS,
        subjects=(Subject(1, computers_subject), Subject(2)),
        classes=(
            SchoolClass(1, "8А", 8, counts[0]),
            SchoolClass(2, "8Б", 8, counts[1]),
        ),
    )


def run(inp, room):
    return check(inp, solved(lesson(1, 1, 1, 1, room=room)))


def test_computer_subject_in_a_computer_room_passes():
    inp = fit_school([course(1, [1], part(1, needs_room=True))])
    assert run(inp, 2) == []
    assert run(inp, 3) == []


def test_computer_subject_in_a_room_without_computers_is_flagged():
    inp = fit_school([course(1, [1], part(1, needs_room=True))], counts=(10, 10))
    assert run(inp, 1) == [
        v("room_match", "room_no_computers", ("course", 1), ("part", 1), ("room", 1))
    ]


def test_computers_need_a_room_even_if_the_part_says_no():
    inp = fit_school([course(1, [1], part(1))])
    assert run(inp, None) == [
        v("room_match", "room_missing", ("course", 1), ("part", 1))
    ]
    assert run(inp, 3) == []


def test_plain_subject_may_use_a_room_without_computers():
    inp = fit_school(
        [course(1, [1], part(1, needs_room=True))], computers_subject=False
    )
    assert run(inp, 1) == [
        v("room_match", "room_too_small", ("course", 1), ("part", 1), ("room", 1))
    ]  # 25 students, 20 seats: only the seats are wrong
    inp = fit_school(
        [course(1, [1], part(1, needs_room=True))],
        computers_subject=False,
        counts=(20, 10),
    )
    assert run(inp, 1) == []


def test_too_few_seats_is_flagged_and_equal_seats_pass():
    inp = fit_school([course(1, [1], part(1, needs_room=True))], counts=(31, 10))
    assert run(inp, 2) == [
        v("room_match", "room_too_small", ("course", 1), ("part", 1), ("room", 2))
    ]
    inp = fit_school([course(1, [1], part(1, needs_room=True))], counts=(30, 10))
    assert run(inp, 2) == []


def test_room_without_a_seat_count_has_no_limit():
    inp = fit_school([course(1, [1], part(1, needs_room=True))], counts=(500, 10))
    assert run(inp, 3) == []


def test_unknown_student_count_skips_the_rule():
    inp = fit_school([course(1, [1], part(1, needs_room=True))], counts=(None, 10))
    assert run(inp, 2) == []


def test_joint_lesson_adds_up_the_classes():
    joint = course(1, [1], part(1, groups=(1, 5), needs_room=True))
    flagged = [
        v("room_match", "room_too_small", ("course", 1), ("part", 1), ("room", 2))
    ]
    assert run(fit_school([joint], counts=(25, 10)), 2) == flagged  # 35 > 30
    assert run(fit_school([joint], counts=(20, 10)), 2) == []  # 30 <= 30
    # one of the two classes has no count: unknown, so skipped
    assert run(fit_school([joint], counts=(25, None)), 2) == []


def test_sub_group_counts_its_own_students():
    inp = fit_school(
        [course(1, [1], part(1, groups=(2,), needs_room=True))],
        computers_subject=False,
        counts=(40, 10),
    )
    sub = (
        StudentGroup(1, 1, True),
        StudentGroup(2, 1, False, split="групи", student_count=15),
        StudentGroup(3, 1, False, split="групи"),
        StudentGroup(4, 1, False, split="езици"),
        StudentGroup(5, 2, True),
    )
    assert run(replace(inp, groups=sub), 1) == []  # 15 <= 20, the class has 40
    # group 3 has no count: unknown, skipped
    inp3 = replace(
        inp,
        groups=sub,
        courses=(course(1, [1], part(1, groups=(3,), needs_room=True)),),
    )
    assert run(inp3, 1) == []
    # 21 students > 20 seats
    big = replace(
        inp,
        groups=(
            *sub[:1],
            StudentGroup(2, 1, False, split="групи", student_count=21),
            *sub[2:],
        ),
    )
    assert run(big, 1) == [
        v("room_match", "room_too_small", ("course", 1), ("part", 1), ("room", 1))
    ]
