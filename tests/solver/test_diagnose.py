"""FIX-UKTC: weekly loads that cannot fit the hard rules are proven by counting, at once.

Every verdict is cross-checked against the full CP-SAT model (built without the shortcut in
``solve``), so the counting can never claim a conflict the solver would not find.
"""

from dataclasses import replace

from solver import _build, run, solve
from solver.diagnose import find_overloads
from tests.solver.test_solver import SOLVED, SPLIT, make
from tests.solver.test_solver_constraints import build, hard, soft


def cp_sat_status(inp):
    """The status of the model itself, without the counting shortcut."""
    return run(_build(inp), 5, 1, 0).status


def one_teacher(n, days=(1, 2), periods=(1, 2, 3)):
    """One teacher gives ``n`` one-period lessons, each to its own class."""
    return make(
        list(days),
        list(periods),
        [([1], [c], [1]) for c in range(1, n + 1)],
        classes=n,
    )


def test_teacher_max_per_day_and_rest_together_overload_the_teacher():
    # 6 slots; the rest bans 1 of them and the cap allows 2 a day: 2 + 2 = 4 < 5 lessons.
    inp = build(
        one_teacher(5),
        hard(1, "teacher_max_lessons_per_day", teacher_id=1, max=2),
        hard(
            2,
            "teacher_unavailable_slots",
            teacher_id=1,
            slots=[{"day": 1, "period": 3}],
        ),
        hard(3, "teacher_max_gaps", teacher_id=1, max=0),  # never part of the proof
    )
    (found,) = find_overloads(inp)
    assert (found.kind, found.entity_id, found.load, found.capacity) == (
        "teacher",
        1,
        5,
        4,
    )
    assert found.constraint_ids == (1,)  # the cap alone already makes 4 < 5
    assert cp_sat_status(inp) == "INFEASIBLE"
    res = solve(inp, time_limit_s=5, num_workers=1)
    assert res.status == "INFEASIBLE" and res.conflicting_constraint_ids == (1,)


def test_blame_is_every_constraint_that_is_needed_and_no_other():
    # 5 lessons, 3 days of 3 slots. The cap of 2 a day alone leaves 6 slots, the rest of day 3
    # alone leaves 6, together 4 < 5. The rest of another teacher is no part of it.
    inp = build(
        one_teacher(5, days=(1, 2, 3)),
        hard(1, "teacher_max_lessons_per_day", teacher_id=1, max=2),
        hard(
            2,
            "teacher_unavailable_slots",
            teacher_id=1,
            slots=[{"day": 3, "period": p} for p in (1, 2, 3)],
        ),
        hard(
            3,
            "teacher_unavailable_slots",
            teacher_id=2,
            slots=[{"day": 1, "period": 1}],
        ),
    )
    (found,) = find_overloads(inp)
    assert found.constraint_ids == (1, 2) and found.load == 5 and found.capacity == 4
    assert cp_sat_status(inp) == "INFEASIBLE"


def test_a_day_with_fewer_free_slots_than_the_minimum_can_not_be_used():
    # Day 2 has 1 free slot but a day with lessons needs 2: only day 1 (3 slots) is usable.
    inp = build(
        one_teacher(4),
        hard(1, "teacher_min_lessons_per_day", teacher_id=1, min=2),
        hard(
            2,
            "teacher_unavailable_slots",
            teacher_id=1,
            slots=[{"day": 2, "period": p} for p in (2, 3)],
        ),
    )
    (found,) = find_overloads(inp)
    assert found.capacity == 3 and found.constraint_ids == (1, 2)
    assert cp_sat_status(inp) == "INFEASIBLE"
    # Three lessons do fit (all on day 1), and the counting agrees.
    fits = build(one_teacher(3), *inp.constraints)
    assert find_overloads(fits) == [] and cp_sat_status(fits) in SOLVED


def test_available_days_count_like_a_full_rest():
    inp = build(
        one_teacher(4), hard(1, "teacher_available_days", teacher_id=1, days=[1])
    )
    (found,) = find_overloads(inp)
    assert found.capacity == 3 and found.constraint_ids == (1,)
    assert cp_sat_status(inp) == "INFEASIBLE"


def test_a_load_that_exactly_fits_is_not_an_overload():
    inp = build(
        one_teacher(4), hard(1, "teacher_max_lessons_per_day", teacher_id=1, max=2)
    )
    assert find_overloads(inp) == []
    assert solve(inp, time_limit_s=5, num_workers=1).status in SOLVED


def test_soft_constraints_never_make_an_overload():
    inp = build(
        one_teacher(5), soft(1, "teacher_max_lessons_per_day", 5, teacher_id=1, max=2)
    )
    assert find_overloads(inp) == []
    assert solve(inp, time_limit_s=5, num_workers=1).status in SOLVED


def test_a_base_data_overload_is_left_to_the_solver():
    # 7 lessons in 6 slots, no user constraint at all: no constraint to blame.
    inp = one_teacher(7)
    assert find_overloads(inp) == []
    res = solve(inp, time_limit_s=5, num_workers=1)
    assert res.status == "INFEASIBLE" and res.conflicting_constraint_ids == ()
    # A harmless constraint must not be blamed for it either.
    harmless = build(inp, hard(1, "teacher_max_lessons_per_day", teacher_id=1, max=3))
    assert find_overloads(harmless) == []


def rest(id, class_id, *slots):
    return hard(
        id,
        "class_unavailable_slots",
        class_id=class_id,
        slots=[{"day": d, "period": p} for d, p in slots],
    )


def test_class_rest_leaving_fewer_slots_than_lessons_is_an_overload():
    inp = build(
        make([1], [1, 2, 3], [([1], [1], [t]) for t in (1, 2, 3)], classes=1),
        rest(1, 1, (1, 3)),
        rest(2, 1, (1, 3), (1, 2)),  # a second rest of the class; with 1 it is 1 slot
    )
    (found,) = find_overloads(inp)
    assert (found.kind, found.entity_id, found.load, found.capacity) == (
        "class",
        1,
        3,
        1,
    )
    assert found.constraint_ids == (
        2,
    )  # constraint 2 alone is enough (it includes (1,3))
    assert cp_sat_status(inp) == "INFEASIBLE"
    res = solve(inp, time_limit_s=5, num_workers=1)
    assert res.conflicting_constraint_ids == (2,)


def test_class_load_is_that_of_the_worst_student_not_of_the_whole_class():
    # Whole-class lesson + one lesson for each of two groups of the same split, 2 free slots:
    # a student sits through 2 lessons, never 3.
    base = make(
        [1],
        [1, 2, 3],
        [([1], [1], [1]), ([1], [2], [2]), ([1], [3], [3])],
        groups=SPLIT,
        classes=1,
    )
    fits = build(base, rest(1, 1, (1, 3)))
    assert find_overloads(fits) == [] and cp_sat_status(fits) in SOLVED
    # One more whole-class lesson: the student is now at 3 lessons in 2 slots.
    more = make(
        [1],
        [1, 2, 3],
        [([1], [1], [1]), ([1], [1], [4]), ([1], [2], [2]), ([1], [3], [3])],
        groups=SPLIT,
        classes=1,
    )
    over = build(more, rest(1, 1, (1, 3)))
    (found,) = find_overloads(over)
    assert (found.load, found.capacity) == (3, 2)
    assert cp_sat_status(over) == "INFEASIBLE"


def test_weeks_are_counted_separately():
    def weekly(*weeks):
        inp = make(
            [1],
            [1, 2, 3],
            [([1], [1], [t]) for t in range(1, len(weeks) + 1)],
            classes=1,
        )
        courses = tuple(
            replace(c, week=w) for c, w in zip(inp.courses, weeks, strict=True)
        )
        return build(replace(inp, courses=courses), rest(1, 1, (1, 3)))

    # A + B in the same slot is one lesson a week for the student: 2 slots are enough.
    assert find_overloads(weekly("A", "B")) == []
    assert find_overloads(weekly(None, "A", "B")) == []
    # Two every-week lessons and one week-A lesson: 3 in week A, only 2 slots.
    (found,) = find_overloads(weekly(None, None, "A"))
    assert found.load == 3 and found.capacity == 2
    assert cp_sat_status(weekly(None, None, "A")) == "INFEASIBLE"
    assert cp_sat_status(weekly(None, "A", "B")) in SOLVED
