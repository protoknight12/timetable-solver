"""One group of tests per constraint type (BE-07).

Small inputs where the constraint changes the result. A solved result is
re-checked with the test-only ``verify`` (hard constraints hold, objective
equals the independently computed soft penalty).
"""

from dataclasses import replace

from schema.contract import Constraint
from solver import add_constraints, new_model, solve
from tests.solver.test_solver import SOLVED, SPLIT, make
from tests.solver.verify import soft_penalty, violations


def hard(id, type, **params):
    return Constraint(id, type, True, params)


def soft(id, type, weight, **params):
    return Constraint(id, type, False, params, weight)


def build(inp, *cons, grades=None, rooms_needed=False):
    classes = inp.classes
    if grades:
        classes = tuple(replace(c, grade=grades[c.id]) for c in classes)
    courses = inp.courses
    if rooms_needed:
        courses = tuple(
            replace(c, parts=tuple(replace(p, needs_room=True) for p in c.parts))
            for c in courses
        )
    return replace(inp, classes=classes, courses=courses, constraints=cons)


def go(inp):
    res = solve(inp, time_limit_s=5, num_workers=1)
    if res.status in SOLVED:
        assert violations(inp, res) == []
        assert (res.objective or 0) == soft_penalty(inp, res)
    return res


def fix(id, course_id, *blocks):
    return hard(
        id,
        "fixed_slot",
        course_id=course_id,
        blocks=[{"day": d, "start_period": s} for d, s in blocks],
    )


def slots(res, course_id):
    return sorted(
        {(x.day, x.start_period) for x in res.lessons if x.course_id == course_id}
    )


def test_no_soft_constraint_means_no_objective():
    assert go(build(make([1], [1], [([1], [1], [1])], classes=1))).objective is None
    assert (
        go(
            build(
                make([1], [1], [([1], [1], [1])], classes=1),
                hard(1, "no_gaps", scope="all"),
            )
        ).objective
        is None
    )


def test_every_constraint_has_an_enforcement_literal():
    inp = build(
        make([1], [1], [([1], [1], [1])], classes=1),
        hard(4, "no_gaps", scope="all"),
        soft(9, "no_gaps", 1, scope="all"),
    )
    tm = new_model(inp)
    add_constraints(tm)
    assert set(tm.enforce) == {4, 9}


# --- fixed_slot -------------------------------------------------------------


def test_fixed_slot_places_each_block():
    inp = build(
        make([1, 2], [1, 2, 3, 4], [([2, 1], [1], [1])], classes=1),
        fix(1, 1, (1, 3), (2, 1)),
    )
    res = go(inp)
    assert res.status in SOLVED
    # one row per block, in block order: the 2-block on day 1, the 1-block on day 2
    assert [(x.day, x.start_period, x.length) for x in res.lessons] == [
        (1, 3, 2),
        (2, 1, 1),
    ]


def test_fixed_slot_clash_is_infeasible():
    two = [([1], [1], [1]), ([1], [2], [1])]  # one teacher, two classes
    inp = make([1], [1, 2], two)
    assert go(build(inp, fix(1, 1, (1, 1)), fix(2, 2, (1, 2)))).status in SOLVED
    assert go(build(inp, fix(1, 1, (1, 1)), fix(2, 2, (1, 1)))).status == "INFEASIBLE"


def test_soft_fixed_slot_pays_for_each_block_elsewhere():
    two = [([1], [1], [1]), ([1], [2], [1])]
    inp = make([1], [1, 2], two)
    pref = soft(3, "fixed_slot", 4, course_id=1, blocks=[{"day": 1, "start_period": 1}])
    assert go(build(inp, pref)).objective == 0
    res = go(build(inp, fix(1, 2, (1, 1)), pref))  # course 2 takes the preferred slot
    assert res.objective == 4 and slots(res, 1) == [(1, 2)]


# --- teacher_available_days -------------------------------------------------


def test_teacher_available_days_hard():
    one = make([1, 2, 3], [1], [([1], [1], [1])], classes=1)
    res = go(build(one, hard(1, "teacher_available_days", teacher_id=1, days=[3])))
    assert slots(res, 1) == [(3, 1)]
    two = make([1, 2], [1], [([1], [1], [1]), ([1], [2], [1])])  # needs two days
    assert (
        go(build(two, hard(1, "teacher_available_days", teacher_id=1, days=[1]))).status
        == "INFEASIBLE"
    )


def test_teacher_available_days_soft_pays_per_period_on_other_days():
    two = make([1, 2], [1], [([1], [1], [1]), ([1], [2], [1])])
    res = go(build(two, soft(1, "teacher_available_days", 3, teacher_id=1, days=[1])))
    assert res.objective == 3
    block = make([1, 2], [1, 2], [([2], [1], [1])], classes=1)
    forced = build(
        block,
        fix(1, 1, (2, 1)),
        soft(2, "teacher_available_days", 3, teacher_id=1, days=[1]),
    )
    assert go(forced).objective == 6  # a 2-period block on day 2 costs 2 x 3


# --- teacher_unavailable_slots ----------------------------------------------


def test_teacher_unavailable_slots_hard():
    one = make([1], [1, 2], [([1], [1], [1])], classes=1)
    res = go(
        build(
            one,
            hard(
                1,
                "teacher_unavailable_slots",
                teacher_id=1,
                slots=[{"day": 1, "period": 1}],
            ),
        )
    )
    assert slots(res, 1) == [(1, 2)]
    two = make([1], [1, 2], [([1], [1], [1]), ([1], [2], [1])])  # both periods needed
    inp = build(
        two,
        hard(
            1,
            "teacher_unavailable_slots",
            teacher_id=1,
            slots=[{"day": 1, "period": 1}],
        ),
    )
    assert go(inp).status == "INFEASIBLE"


def test_teacher_unavailable_slots_soft():
    two = make([1], [1, 2], [([1], [1], [1]), ([1], [2], [1])])
    res = go(
        build(
            two,
            soft(
                1,
                "teacher_unavailable_slots",
                4,
                teacher_id=1,
                slots=[{"day": 1, "period": 1}],
            ),
        )
    )
    assert res.objective == 4


# --- room_type_required -----------------------------------------------------

ROOMS = [{"id": 1, "type": "regular"}, {"id": 2, "type": "lab"}]


def test_room_type_required_hard_turns_no_room_into_room_of_type():
    inp = make([1], [1], [([1], [1], [1])], rooms=ROOMS, classes=1)
    assert [x.room_id for x in go(inp).lessons] == [None]
    for params in ({"subject_id": 1}, {"course_id": 1}):
        res = go(build(inp, hard(1, "room_type_required", room_type="lab", **params)))
        assert [x.room_id for x in res.lessons] == [2]


def test_room_type_required_hard_intersects_with_the_part_and_the_room():
    rooms = [{"id": 1, "type": "regular"}, {"id": 2, "type": "lab"}]
    needs_regular = make([1], [1], [([1], [1], [1], "regular")], rooms=rooms, classes=1)
    assert (
        go(
            build(
                needs_regular,
                hard(1, "room_type_required", subject_id=1, room_type="lab"),
            )
        ).status
        == "INFEASIBLE"
    )
    two = make(
        [1], [1], [([1], [1], [1]), ([1], [2], [2])], rooms=rooms
    )  # one lab, one slot
    inp = build(two, hard(1, "room_type_required", subject_id=1, room_type="lab"))
    assert go(inp).status == "INFEASIBLE"
    assert go(replace(inp, periods=(1, 2))).status in SOLVED


def test_room_type_required_soft_counts_part_blocks_in_other_rooms():
    two = make([1], [1], [([1], [1], [1]), ([1], [2], [2])], rooms=ROOMS)
    pref = soft(1, "room_type_required", 5, subject_id=1, room_type="lab")
    assert go(build(two, pref, rooms_needed=True)).objective == 5  # one lab, one slot
    assert (
        go(replace(build(two, pref, rooms_needed=True), periods=(1, 2))).objective == 0
    )
    # a part with no room is not touched
    assert go(build(two, pref)).objective == 0


# --- max_lessons_per_day ----------------------------------------------------


def test_max_lessons_per_day_hard_and_soft():
    three = make([1, 2], [1, 2, 3], [([1, 1, 1], [1], [1])], classes=1)
    scope = {"class_id": 1}
    assert (
        go(build(three, hard(1, "max_lessons_per_day", scope=scope, max=1))).status
        == "INFEASIBLE"
    )
    assert (
        go(build(three, hard(1, "max_lessons_per_day", scope=scope, max=2))).status
        in SOLVED
    )
    assert (
        go(
            build(three, soft(1, "max_lessons_per_day", 2, scope=scope, max=1))
        ).objective
        == 2
    )


def test_max_lessons_per_day_grade_scope():
    inp = make([1, 2], [1, 2, 3], [([1, 1], [1], [1]), ([1], [2], [2])])
    bounded = build(
        inp,
        hard(1, "max_lessons_per_day", scope={"grade": 9}, max=1),
        grades={1: 9, 2: 10},
    )
    assert (
        go(bounded).status in SOLVED
    )  # class 2 is another grade: still 2 days for class 1
    same = build(
        inp,
        hard(1, "max_lessons_per_day", scope={"grade": 9}, max=1),
        grades={1: 9, 2: 9},
    )
    assert go(same).status in SOLVED
    one_day = replace(same, days=(1,))
    assert go(one_day).status == "INFEASIBLE"
    assert (
        go(replace(bounded, days=(1,))).status == "INFEASIBLE"
    )  # class 1 alone has 2 on one day


def test_max_lessons_per_day_counts_a_split_student_not_the_whole_class():
    # Whole class in period 1; both groups in parallel in period 2. A pupil sits
    # through 2 periods, though the class has 3 lessons.
    courses = [([1], [1], [1]), ([1], [2], [2]), ([1], [3], [3])]
    inp = make([1], [1, 2], courses, groups=SPLIT, classes=1)
    scope = {"class_id": 1}
    assert (
        go(build(inp, hard(1, "max_lessons_per_day", scope=scope, max=2))).status
        in SOLVED
    )
    assert (
        go(build(inp, hard(1, "max_lessons_per_day", scope=scope, max=1))).status
        == "INFEASIBLE"
    )


# --- max_same_subject_per_day / teacher_max_lessons_per_day -----------------


def with_subjects(inp, *subject_ids):
    """Course i teaches subject_ids[i] (the helper ``make`` gives all subject 1)."""
    courses = tuple(
        replace(c, parts=tuple(replace(p, subject_id=s) for p in c.parts))
        for c, s in zip(inp.courses, subject_ids, strict=True)
    )
    known = tuple(replace(inp.subjects[0], id=i) for i in set(subject_ids))
    return replace(inp, courses=courses, subjects=known)


def test_max_same_subject_per_day_hard_and_soft():
    # Three 1-period lessons of subject 1 plus one of subject 2, two days.
    inp = with_subjects(
        make(
            [1, 2], [1, 2, 3, 4], [([1], [1], [1])] * 3 + [([1], [1], [2])], classes=1
        ),
        1,
        1,
        1,
        2,
    )
    scope = {"class_id": 1}
    cap = lambda n: hard(
        1, "max_same_subject_per_day", scope=scope, subject_id=1, max=n
    )
    assert go(build(inp, cap(2))).status in SOLVED
    assert go(build(inp, cap(1))).status == "INFEASIBLE"  # 3 lessons, 2 days
    assert go(build(replace(inp, days=(1,)), cap(3))).status in SOLVED
    assert go(build(replace(inp, days=(1,)), cap(2))).status == "INFEASIBLE"
    other = hard(1, "max_same_subject_per_day", scope=scope, subject_id=2, max=1)
    assert go(build(replace(inp, days=(1,)), other)).status in SOLVED
    s = soft(1, "max_same_subject_per_day", 2, scope=scope, subject_id=1, max=1)
    res = go(build(inp, s))  # go() checks objective == independent penalty
    assert (
        res.status in SOLVED and res.objective == 2
    )  # 3 lessons over 2 days: one extra


def test_teacher_max_lessons_per_day_hard_and_soft():
    inp = make([1, 2], [1, 2, 3], [([1], [1], [1])] * 3, classes=1)
    cap = lambda n: hard(1, "teacher_max_lessons_per_day", teacher_id=1, max=n)
    assert go(build(inp, cap(2))).status in SOLVED
    assert go(build(inp, cap(1))).status == "INFEASIBLE"  # 3 lessons, 2 days
    assert go(build(replace(inp, days=(1,)), cap(3))).status in SOLVED
    s = soft(1, "teacher_max_lessons_per_day", 3, teacher_id=1, max=1)
    res = go(build(inp, s))
    assert res.status in SOLVED and res.objective == 3


# --- no_gaps ----------------------------------------------------------------


def one_class(*pos, periods=(1, 2, 3, 4)):
    """Class 1, one day, one block-1 course per entry of ``pos`` (period, teacher)."""
    return make([1], list(periods), [([1], [1], [t]) for _, t in pos], classes=1)


def test_leading_and_trailing_free_periods_are_not_gaps():
    inp = one_class((3, 1), (4, 2))
    for c in (hard(1, "no_gaps", scope="all"), soft(1, "no_gaps", 9, scope="all")):
        res = go(build(inp, fix(7, 1, (1, 3)), fix(8, 2, (1, 4)), c))
        assert res.status in SOLVED and (res.objective or 0) == 0


def test_no_gaps_hard_forced_gap_is_infeasible_and_free_choice_closes_it():
    inp = one_class((1, 1), (3, 2))
    pinned = build(inp, fix(7, 1, (1, 1)), fix(8, 2, (1, 3)))
    assert (
        go(
            replace(
                pinned,
                constraints=(*pinned.constraints, hard(1, "no_gaps", scope="all")),
            )
        ).status
        == "INFEASIBLE"
    )
    res = go(build(inp, hard(1, "no_gaps", scope="all")))
    assert res.status in SOLVED and abs(slots(res, 1)[0][1] - slots(res, 2)[0][1]) == 1


def test_no_gaps_soft_counts_each_gap_once_with_its_weight():
    pinned = build(one_class((1, 1), (4, 2)), fix(7, 1, (1, 1)), fix(8, 2, (1, 4)))
    with_soft = replace(
        pinned, constraints=(*pinned.constraints, soft(1, "no_gaps", 7, scope="all"))
    )
    assert go(with_soft).objective == 14  # periods 2 and 3 are gaps
    free = build(one_class((1, 1), (4, 2)), soft(1, "no_gaps", 7, scope="all"))
    assert go(free).objective == 0  # the solver puts the two lessons next to each other


def test_no_gaps_scope_class_and_grade():
    two = make(
        [1],
        [1, 2, 3],
        [([1], [1], [1]), ([1], [1], [2]), ([1], [2], [3]), ([1], [2], [4])],
    )
    pins = (fix(7, 1, (1, 1)), fix(8, 2, (1, 3)), fix(9, 3, (1, 1)), fix(10, 4, (1, 2)))
    gaps = lambda scope, **kw: (
        go(build(two, *pins, soft(1, "no_gaps", 2, scope=scope), **kw)).objective
    )
    assert (
        gaps({"class_id": 1}) == 2 and gaps({"class_id": 2}) == 0 and gaps("all") == 2
    )
    assert gaps({"grade": 9}, grades={1: 9, 2: 10}) == 2
    assert gaps({"grade": 10}, grades={1: 9, 2: 10}) == 0


def test_no_gaps_counts_each_student_combination_separately():
    # Whole class in period 1; the groups are in period 3, so the free period 2 is
    # a gap for the Група 1 pupil and for the Група 2 pupil: 2 gaps, not 1.
    both = [([1], [1], [1]), ([1], [2], [2]), ([1], [3], [3])]
    inp = make([1], [1, 2, 3], both, groups=SPLIT, classes=1)
    pins = (fix(7, 1, (1, 1)), fix(8, 2, (1, 3)), fix(9, 3, (1, 3)))
    assert go(build(inp, *pins, soft(1, "no_gaps", 3, scope="all"))).objective == 6
    # Only Група 1 has a lesson at 3: the Група 2 pupil has period 1 only, no gap.
    only_one = make([1], [1, 2, 3], both[:2], groups=SPLIT, classes=1)
    assert (
        go(build(only_one, *pins[:2], soft(1, "no_gaps", 3, scope="all"))).objective
        == 3
    )


# --- day_starts_first_period ------------------------------------------------


def test_day_starts_first_period_hard_moves_a_late_start_and_soft_pays_per_day():
    inp = one_class((3, 1), (4, 2))
    res = go(build(inp, hard(1, "day_starts_first_period", scope="all")))
    assert (
        res.status in SOLVED and min(s for _, s in slots(res, 1) + slots(res, 2)) == 1
    )
    pinned = build(inp, fix(7, 1, (1, 3)), fix(8, 2, (1, 4)))
    late = lambda c: replace(pinned, constraints=(*pinned.constraints, c))
    assert go(late(hard(1, "day_starts_first_period", scope="all"))).status == (
        "INFEASIBLE"
    )
    assert go(late(soft(1, "day_starts_first_period", 5, scope="all"))).objective == 5


def test_day_starts_first_period_is_per_student_combination():
    # Group 1 starts at period 1, group 2 only at period 2: the group 2 pupil starts late.
    both = [([1], [2], [1]), ([1], [3], [2])]
    inp = make([1], [1, 2, 3], both, groups=SPLIT, classes=1)
    pins = (fix(7, 1, (1, 1)), fix(8, 2, (1, 2)))
    con = soft(1, "day_starts_first_period", 4, scope="all")
    assert go(build(inp, *pins, con)).objective == 4


def test_day_without_lessons_does_not_count_as_a_late_start():
    inp = make([1, 2], [1, 2], [([1], [1], [1])], classes=1)
    res = go(
        build(inp, fix(7, 1, (2, 1)), hard(1, "day_starts_first_period", scope="all"))
    )
    assert res.status in SOLVED


# --- periods_reserved_for_subjects ------------------------------------------


def two_subjects(periods=(1, 2, 3)):
    """Class 1, one day: course 1 is subject 1 (e.g. maths), course 2 subject 2 (PE)."""
    inp = make([1], list(periods), [([1], [1], [1]), ([1], [1], [2])], classes=1)
    pe = replace(
        inp.courses[1], parts=(replace(inp.courses[1].parts[0], subject_id=2),)
    )
    return replace(
        inp,
        subjects=(*inp.subjects, replace(inp.subjects[0], id=2)),
        courses=(inp.courses[0], pe),
    )


def test_reserved_periods_hold_only_the_listed_subjects():
    inp = two_subjects()
    con = hard(1, "periods_reserved_for_subjects", periods=[2, 3], subject_ids=[2])
    res = go(build(inp, con))
    assert res.status in SOLVED and slots(res, 1) == [(1, 1)]
    # PE pinned to period 1 leaves maths no period at all.
    assert go(build(inp, fix(7, 2, (1, 1)), con)).status == "INFEASIBLE"


def test_reserved_periods_soft_pays_per_period():
    inp = two_subjects()
    con = soft(1, "periods_reserved_for_subjects", 3, periods=[1], subject_ids=[2])
    assert go(build(inp, fix(7, 1, (1, 1)), con)).objective == 3
    assert go(build(inp, con)).objective == 0


# --- BE-19: class / subject slots, teacher gaps, teacher minimum ----------------


def test_class_unavailable_slots_hard_and_soft():
    two = make([1], [1, 2], [([1], [1], [1]), ([1], [2], [2])], classes=2)
    ban = {"class_id": 1, "slots": [{"day": 1, "period": 1}]}
    res = go(build(two, hard(1, "class_unavailable_slots", **ban)))
    assert slots(res, 1) == [(1, 2)] and res.status in SOLVED
    both = make([1], [1, 2], [([1], [1], [1]), ([1], [1], [2])], classes=1)
    assert go(build(both, hard(1, "class_unavailable_slots", **ban))).status == (
        "INFEASIBLE"
    )  # two lessons, one free slot
    assert go(build(both, soft(1, "class_unavailable_slots", 4, **ban))).objective == 4


def test_class_unavailable_slots_bans_split_groups_too():
    groups = [
        {"id": 1, "class_id": 1, "is_whole": True},
        {"id": 2, "class_id": 1, "is_whole": False, "split": "s"},
    ]
    inp = make([1], [1, 2], [([1], [2], [1])], groups=groups, classes=1)
    c = hard(1, "class_unavailable_slots", class_id=1, slots=[{"day": 1, "period": 1}])
    assert slots(go(build(inp, c)), 1) == [(1, 2)]


def test_subject_unavailable_slots_hard_and_soft():
    inp = make([1], [1, 2], [([1], [1], [1])], classes=1)
    ban = {"subject_id": 1, "slots": [{"day": 1, "period": 1}]}
    assert slots(go(build(inp, hard(1, "subject_unavailable_slots", **ban))), 1) == [
        (1, 2)
    ]
    both = make([1], [1, 2], [([1], [1], [1]), ([1], [1], [2])], classes=1)
    assert (
        go(build(both, soft(1, "subject_unavailable_slots", 3, **ban))).objective == 3
    )


def test_teacher_max_gaps_hard_and_soft():
    pin = (fix(7, 1, (1, 1)), fix(8, 2, (1, 4)))  # forced 2-period gap
    inp = one_class((1, 1), (4, 1))
    gaps = lambda n: hard(1, "teacher_max_gaps", teacher_id=1, max=n)
    assert go(build(inp, *pin, gaps(1))).status == "INFEASIBLE"
    assert go(build(inp, *pin, gaps(2))).status in SOLVED
    res = go(build(inp, *pin, soft(1, "teacher_max_gaps", 5, teacher_id=1, max=1)))
    assert res.objective == 5  # one gap over the maximum
    # free choice: the solver closes the gap
    res = go(build(inp, gaps(0)))
    assert res.status in SOLVED and abs(slots(res, 1)[0][1] - slots(res, 2)[0][1]) == 1


def test_teacher_min_lessons_per_day_hard_and_soft():
    inp = make([1, 2], [1, 2], [([1], [1], [1])] * 2, classes=1)
    low = lambda n: hard(1, "teacher_min_lessons_per_day", teacher_id=1, min=n)
    res = go(build(inp, low(2)))  # 2 lessons: both on one day, other day free
    assert res.status in SOLVED and len({x.day for x in res.lessons}) == 1
    assert go(build(inp, low(3))).status == "INFEASIBLE"
    pin = (fix(7, 1, (1, 1)), fix(8, 2, (2, 1)))  # one lesson on each day
    res = go(
        build(inp, *pin, soft(1, "teacher_min_lessons_per_day", 4, teacher_id=1, min=2))
    )
    assert res.objective == 8  # one period short on each of 2 days
