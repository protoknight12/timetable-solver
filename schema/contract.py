"""Plain-data contract between the web app, the solver and the checker.

This module defines exactly what the solver receives (``SolverInput``) and
returns (``SolveResult``). It uses only the standard library and must never
import Flask, SQLAlchemy or anything from ``app/`` (CLAUDE.md rule 2), so both
``solver/`` and ``checker/`` can import it.

The model (TECH_STACK.md section 5):

- A ``Course`` is one scheduled unit. It has ``periods_per_week``, split into
  ``block_sizes`` (e.g. ``[1, 1, 1]``, ``[2, 1]``, ``[2]``). The solver gives
  each block a day and a start period.
- A ``CoursePart`` is what one set of student groups does during that unit
  (subject, groups, teachers, room needs). All parts of a Course happen at
  exactly the same time, by construction.
- A ``StudentGroup`` belongs to one class. Every class has exactly one
  ``is_whole`` group ("цял клас"); splits add non-whole groups ("Група 1").
  Each non-whole group names its ``split`` (e.g. "групи", "езици"). Groups in
  the same split never share students, so they may have lessons at the same
  time. Groups in different splits of one class may share students, so they
  conflict, like the whole class does with every group.
  Groups that are not in any part of a Course simply have no lesson then.

This file only describes and validates data. It does not implement timetable
rules: the solver and the checker each implement those on their own.

Changing anything here changes the contract for both developers (CLAUDE.md
rule 7): agree first, then bump ``CONTRACT_VERSION``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from itertools import combinations
from typing import Any

CONTRACT_VERSION = 2  # 2: StudentGroup.split

SOLVE_STATUSES = ("OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN", "MODEL_INVALID")

# Constraint types the solver and checker understand. The typed params for
# each type are defined with Pydantic in BE-03; this list only gates names.
CONSTRAINT_TYPES = (
    "teacher_available_days",
    "teacher_unavailable_slots",
    "room_type_required",
    "max_lessons_per_day",
    "no_gaps",
    "fixed_slot",
)


class ContractError(ValueError):
    """Raised when data does not fit the contract. ``problems`` lists why."""

    def __init__(self, problems: list[Problem]):
        self.problems = problems
        super().__init__("; ".join(str(p) for p in problems))


@dataclass(frozen=True)
class Problem:
    """One reason the data is not valid.

    ``code`` is stable and meant for translating into Bulgarian in the web
    layer; ``path`` points at the offending item; ``detail`` is for developers.
    """

    code: str
    path: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}: {self.detail} [{self.code}]"


# --------------------------------------------------------------------------
# Parsing helpers (shape and primitive types only)
# --------------------------------------------------------------------------


def _fail(path: str, detail: str) -> ContractError:
    return ContractError([Problem("bad_shape", path, detail)])


def _obj(data: Any, path: str, required: set[str], optional: set[str] = frozenset()):
    if not isinstance(data, dict):
        raise _fail(path, "expected an object")
    missing = required - data.keys()
    if missing:
        raise _fail(path, f"missing keys: {sorted(missing)}")
    unknown = data.keys() - required - optional
    if unknown:
        # Strict on purpose: a typo in a key would otherwise be silently ignored.
        raise _fail(path, f"unknown keys: {sorted(unknown)}")
    return data


def _int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(path, "expected an integer")
    return value


def _opt_int(value: Any, path: str) -> int | None:
    return None if value is None else _int(value, path)


def _bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(path, "expected true or false")
    return value


def _str(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise _fail(path, "expected a string")
    return value


def _opt_str(value: Any, path: str) -> str | None:
    return None if value is None else _str(value, path)


def _ints(value: Any, path: str) -> tuple[int, ...]:
    if not isinstance(value, list | tuple):
        raise _fail(path, "expected a list of integers")
    return tuple(_int(v, f"{path}[{i}]") for i, v in enumerate(value))


def _list(value: Any, path: str) -> list:
    if not isinstance(value, list | tuple):
        raise _fail(path, "expected a list")
    return list(value)


def _jsonable(value: Any) -> Any:
    """Turn tuples into lists so ``to_dict`` output looks like the JSON input."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


# --------------------------------------------------------------------------
# Input entities
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Teacher:
    id: int

    @classmethod
    def from_dict(cls, d: Any, path: str) -> Teacher:
        _obj(d, path, {"id"})
        return cls(id=_int(d["id"], f"{path}.id"))


@dataclass(frozen=True)
class Subject:
    id: int

    @classmethod
    def from_dict(cls, d: Any, path: str) -> Subject:
        _obj(d, path, {"id"})
        return cls(id=_int(d["id"], f"{path}.id"))


@dataclass(frozen=True)
class SchoolClass:
    id: int
    name: str
    # Needed for constraints scoped to a whole grade ({"grade": 9}).
    grade: int | None = None

    @classmethod
    def from_dict(cls, d: Any, path: str) -> SchoolClass:
        _obj(d, path, {"id", "name"}, {"grade"})
        return cls(
            id=_int(d["id"], f"{path}.id"),
            name=_str(d["name"], f"{path}.name"),
            grade=_opt_int(d.get("grade"), f"{path}.grade"),
        )


@dataclass(frozen=True)
class StudentGroup:
    id: int
    class_id: int
    is_whole: bool
    name: str = ""
    # Which split of the class this group belongs to ("групи", "езици").
    # Empty for the whole-class group. Groups of one class with the same split
    # are disjoint; groups with different splits overlap (TECH_STACK §5, group splits).
    split: str = ""

    @classmethod
    def from_dict(cls, d: Any, path: str) -> StudentGroup:
        _obj(d, path, {"id", "class_id", "is_whole"}, {"name", "split"})
        return cls(
            id=_int(d["id"], f"{path}.id"),
            class_id=_int(d["class_id"], f"{path}.class_id"),
            is_whole=_bool(d["is_whole"], f"{path}.is_whole"),
            name=_str(d.get("name", ""), f"{path}.name"),
            split=_str(d.get("split", ""), f"{path}.split"),
        )


@dataclass(frozen=True)
class Room:
    id: int
    type: str
    # How many part-blocks the room can hold in one slot (sports hall: 3).
    concurrent_capacity: int = 1

    @classmethod
    def from_dict(cls, d: Any, path: str) -> Room:
        _obj(d, path, {"id", "type"}, {"concurrent_capacity"})
        return cls(
            id=_int(d["id"], f"{path}.id"),
            type=_str(d["type"], f"{path}.type"),
            concurrent_capacity=_int(
                d.get("concurrent_capacity", 1), f"{path}.concurrent_capacity"
            ),
        )


@dataclass(frozen=True)
class CoursePart:
    """What one set of groups does during a Course.

    Room rule: if ``needs_room`` is true, the candidate rooms are all rooms,
    narrowed by ``room_type`` (if set) and by ``allowed_room_ids`` (if set).
    Both filters apply together. The web layer resolves the subject's default
    room type into ``room_type`` before building this object.
    """

    id: int
    subject_id: int
    group_ids: tuple[int, ...]
    # All listed teachers are present at the same time (co-teaching).
    teacher_ids: tuple[int, ...]
    needs_room: bool = False
    room_type: str | None = None
    allowed_room_ids: tuple[int, ...] | None = None

    @classmethod
    def from_dict(cls, d: Any, path: str) -> CoursePart:
        _obj(
            d,
            path,
            {"id", "subject_id", "group_ids", "teacher_ids"},
            {"needs_room", "room_type", "allowed_room_ids"},
        )
        allowed = d.get("allowed_room_ids")
        return cls(
            id=_int(d["id"], f"{path}.id"),
            subject_id=_int(d["subject_id"], f"{path}.subject_id"),
            group_ids=_ints(d["group_ids"], f"{path}.group_ids"),
            teacher_ids=_ints(d["teacher_ids"], f"{path}.teacher_ids"),
            needs_room=_bool(d.get("needs_room", False), f"{path}.needs_room"),
            room_type=_opt_str(d.get("room_type"), f"{path}.room_type"),
            allowed_room_ids=None
            if allowed is None
            else _ints(allowed, f"{path}.allowed_room_ids"),
        )


@dataclass(frozen=True)
class Course:
    id: int
    periods_per_week: int
    block_sizes: tuple[int, ...]
    parts: tuple[CoursePart, ...]
    label: str = ""

    @classmethod
    def from_dict(cls, d: Any, path: str) -> Course:
        _obj(d, path, {"id", "periods_per_week", "block_sizes", "parts"}, {"label"})
        parts = _list(d["parts"], f"{path}.parts")
        return cls(
            id=_int(d["id"], f"{path}.id"),
            periods_per_week=_int(d["periods_per_week"], f"{path}.periods_per_week"),
            block_sizes=_ints(d["block_sizes"], f"{path}.block_sizes"),
            parts=tuple(
                CoursePart.from_dict(p, f"{path}.parts[{i}]")
                for i, p in enumerate(parts)
            ),
            label=_str(d.get("label", ""), f"{path}.label"),
        )


@dataclass(frozen=True)
class Constraint:
    """A confirmed constraint, as the solver sees it.

    Only confirmed constraints are ever passed in. ``source``, ``status`` and
    ``original_text`` stay in the database; the solver does not need them.
    ``id`` is the database id, so infeasibility reports can name it (BE-11).
    """

    id: int
    type: str
    hard: bool
    params: dict[str, Any]
    weight: int | None = None  # soft constraints only

    @classmethod
    def from_dict(cls, d: Any, path: str) -> Constraint:
        _obj(d, path, {"id", "type", "hard", "params"}, {"weight"})
        if not isinstance(d["params"], dict):
            raise _fail(f"{path}.params", "expected an object")
        return cls(
            id=_int(d["id"], f"{path}.id"),
            type=_str(d["type"], f"{path}.type"),
            hard=_bool(d["hard"], f"{path}.hard"),
            params=dict(d["params"]),
            weight=_opt_int(d.get("weight"), f"{path}.weight"),
        )


@dataclass(frozen=True)
class SolverInput:
    days: tuple[int, ...]
    periods: tuple[int, ...]
    teachers: tuple[Teacher, ...]
    subjects: tuple[Subject, ...]
    classes: tuple[SchoolClass, ...]
    groups: tuple[StudentGroup, ...]
    rooms: tuple[Room, ...]
    courses: tuple[Course, ...]
    constraints: tuple[Constraint, ...] = ()

    @classmethod
    def from_dict(cls, d: Any) -> SolverInput:
        """Parse the shape only. Use ``load_solver_input`` to also check references."""
        keys = {
            "days",
            "periods",
            "teachers",
            "subjects",
            "classes",
            "groups",
            "rooms",
            "courses",
        }
        _obj(d, "input", keys, {"constraints", "contract_version"})
        version = d.get("contract_version", CONTRACT_VERSION)
        if version != CONTRACT_VERSION:
            raise _fail(
                "input.contract_version",
                f"expected {CONTRACT_VERSION}, got {version!r}",
            )

        def many(key: str, item_cls):
            items = _list(d.get(key, []), f"input.{key}")
            return tuple(
                item_cls.from_dict(x, f"{key}[{i}]") for i, x in enumerate(items)
            )

        return cls(
            days=_ints(d["days"], "input.days"),
            periods=_ints(d["periods"], "input.periods"),
            teachers=many("teachers", Teacher),
            subjects=many("subjects", Subject),
            classes=many("classes", SchoolClass),
            groups=many("groups", StudentGroup),
            rooms=many("rooms", Room),
            courses=many("courses", Course),
            constraints=many("constraints", Constraint),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"contract_version": CONTRACT_VERSION, **_jsonable(asdict(self))}

    def problems(self) -> list[Problem]:
        """Consistency and reference checks. An empty list means valid."""
        return _check_input(self)


def load_solver_input(data: Any) -> SolverInput:
    """Parse and fully validate. Raises ``ContractError`` listing every problem."""
    inp = SolverInput.from_dict(data)
    found = inp.problems()
    if found:
        raise ContractError(found)
    return inp


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LessonPlacement:
    """One part of one block of a Course. One row per part per block."""

    course_id: int
    part_id: int
    day: int
    start_period: int
    length: int
    room_id: int | None = None  # None when the part does not need a room

    @classmethod
    def from_dict(cls, d: Any, path: str) -> LessonPlacement:
        _obj(
            d,
            path,
            {"course_id", "part_id", "day", "start_period", "length"},
            {"room_id"},
        )
        return cls(
            course_id=_int(d["course_id"], f"{path}.course_id"),
            part_id=_int(d["part_id"], f"{path}.part_id"),
            day=_int(d["day"], f"{path}.day"),
            start_period=_int(d["start_period"], f"{path}.start_period"),
            length=_int(d["length"], f"{path}.length"),
            room_id=_opt_int(d.get("room_id"), f"{path}.room_id"),
        )


@dataclass(frozen=True)
class SolveResult:
    status: str
    # Rows come in block order: for each course, the rows of one part appear in
    # the order of ``Course.block_sizes``, so row i of a part is block i. Needed
    # to match ``fixed_slot.blocks[i]``. Agreed 14.11.2026; no version bump
    # because the JSON shape is unchanged.
    lessons: tuple[LessonPlacement, ...] = ()
    objective: int | None = None
    solve_time_s: float = 0.0
    # Filled only when status is INFEASIBLE: ids of user constraints that
    # together make the problem impossible (BE-11).
    conflicting_constraint_ids: tuple[int, ...] = ()

    @classmethod
    def from_dict(cls, d: Any) -> SolveResult:
        _obj(
            d,
            "result",
            {"status"},
            {"lessons", "objective", "solve_time_s", "conflicting_constraint_ids"},
        )
        status = _str(d["status"], "result.status")
        if status not in SOLVE_STATUSES:
            raise _fail("result.status", f"unknown status {status!r}")
        time_s = d.get("solve_time_s", 0.0)
        if isinstance(time_s, bool) or not isinstance(time_s, int | float):
            raise _fail("result.solve_time_s", "expected a number")
        lessons = _list(d.get("lessons", []), "result.lessons")
        return cls(
            status=status,
            lessons=tuple(
                LessonPlacement.from_dict(x, f"lessons[{i}]")
                for i, x in enumerate(lessons)
            ),
            objective=_opt_int(d.get("objective"), "result.objective"),
            solve_time_s=float(time_s),
            conflicting_constraint_ids=_ints(
                d.get("conflicting_constraint_ids", []),
                "result.conflicting_constraint_ids",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


# --------------------------------------------------------------------------
# Consistency checks
# --------------------------------------------------------------------------


def _duplicates(ids) -> list[int]:
    return sorted(i for i, n in Counter(ids).items() if n > 1)


def _groups_conflict(a: StudentGroup, b: StudentGroup) -> bool:
    """True if the two groups may share students, so they cannot run at once.

    Used only for validating a single Course here; the solver and the checker
    implement the same rule on their own.
    """
    if a.class_id != b.class_id:
        return False
    return a.is_whole or b.is_whole or a.id == b.id or a.split != b.split


def _check_input(inp: SolverInput) -> list[Problem]:
    out: list[Problem] = []

    def add(code: str, path: str, detail: str) -> None:
        out.append(Problem(code, path, detail))

    # Time grid. Blocks are consecutive periods, so period numbers must be too.
    if not inp.days or len(set(inp.days)) != len(inp.days):
        add("bad_time_grid", "days", "must be a non-empty list without repeats")
    if not inp.periods or list(inp.periods) != list(
        range(inp.periods[0], inp.periods[0] + len(inp.periods))
    ):
        add("bad_time_grid", "periods", "must be consecutive increasing integers")

    # Unique ids per entity (part ids are unique across all courses).
    all_parts = [p for c in inp.courses for p in c.parts]
    for name, items in (
        ("teachers", inp.teachers),
        ("subjects", inp.subjects),
        ("classes", inp.classes),
        ("groups", inp.groups),
        ("rooms", inp.rooms),
        ("courses", inp.courses),
        ("parts", all_parts),
        ("constraints", inp.constraints),
    ):
        dup = _duplicates(x.id for x in items)
        if dup:
            add("duplicate_id", name, f"repeated ids {dup}")

    teacher_ids = {t.id for t in inp.teachers}
    subject_ids = {s.id for s in inp.subjects}
    class_ids = {c.id for c in inp.classes}
    groups = {g.id: g for g in inp.groups}
    rooms = {r.id: r for r in inp.rooms}
    course_ids = {c.id for c in inp.courses}

    # Groups: each class has exactly one whole-class group.
    for i, g in enumerate(inp.groups):
        if g.class_id not in class_ids:
            add("unknown_reference", f"groups[{i}].class_id", f"no class {g.class_id}")
    whole_count = Counter(g.class_id for g in inp.groups if g.is_whole)
    for i, g in enumerate(inp.groups):
        if g.is_whole and g.split:
            add(
                "whole_group_split",
                f"groups[{i}].split",
                "the whole class has no split",
            )
    for i, c in enumerate(inp.classes):
        if whole_count[c.id] != 1:
            add(
                "whole_group_count",
                f"classes[{i}]",
                f"class {c.id} has {whole_count[c.id]} whole-class groups, expected 1",
            )

    for i, r in enumerate(inp.rooms):
        if r.concurrent_capacity < 1:
            add("bad_capacity", f"rooms[{i}].concurrent_capacity", "must be at least 1")

    for ci, course in enumerate(inp.courses):
        _check_course(
            course, f"courses[{ci}]", inp, add, teacher_ids, subject_ids, groups, rooms
        )

    for i, con in enumerate(inp.constraints):
        _check_constraint(
            con, f"constraints[{i}]", inp, add, teacher_ids, subject_ids, course_ids
        )

    return out


def _check_course(
    course, path, inp, add, teacher_ids, subject_ids, groups, rooms
) -> None:
    if course.periods_per_week < 1:
        add("bad_blocks", f"{path}.periods_per_week", "must be at least 1")
    if not course.block_sizes or any(b < 1 for b in course.block_sizes):
        add(
            "bad_blocks",
            f"{path}.block_sizes",
            "must be a non-empty list of positive sizes",
        )
    elif sum(course.block_sizes) != course.periods_per_week:
        add("bad_blocks", f"{path}.block_sizes", "must add up to periods_per_week")
    if any(b > len(inp.periods) for b in course.block_sizes):
        add(
            "bad_blocks", f"{path}.block_sizes", "a block is longer than the school day"
        )
    # Two blocks of one Course may share a day, even back to back (no gap rule,
    # decided 14.11.2026, TECH_STACK §15 q10). Whether they fit
    # is the solver's job, not a shape check.
    if not course.parts:
        add("empty_course", f"{path}.parts", "a course needs at least one part")

    for pi, part in enumerate(course.parts):
        pp = f"{path}.parts[{pi}]"
        if part.subject_id not in subject_ids:
            add(
                "unknown_reference", f"{pp}.subject_id", f"no subject {part.subject_id}"
            )
        if not part.group_ids:
            add("empty_part", f"{pp}.group_ids", "a part needs at least one group")
        # ASSUMPTION: every lesson has at least one teacher.
        if not part.teacher_ids:
            add("empty_part", f"{pp}.teacher_ids", "a part needs at least one teacher")
        for gid in part.group_ids:
            if gid not in groups:
                add("unknown_reference", f"{pp}.group_ids", f"no group {gid}")
        for tid in part.teacher_ids:
            if tid not in teacher_ids:
                add("unknown_reference", f"{pp}.teacher_ids", f"no teacher {tid}")
        if len(set(part.teacher_ids)) != len(part.teacher_ids):
            add("duplicate_id", f"{pp}.teacher_ids", "a teacher is listed twice")
        _check_part_room(part, pp, add, rooms)

    # Parts of one Course run at the same time, so across the whole Course no
    # teacher may appear twice and no two groups may conflict.
    seen_teachers: Counter[int] = Counter(
        t for p in course.parts for t in set(p.teacher_ids)
    )
    shared = sorted(t for t, n in seen_teachers.items() if n > 1)
    if shared:
        add(
            "shared_teacher",
            f"{path}.parts",
            f"teachers {shared} are in two parts at once",
        )

    course_groups = [
        groups[g] for p in course.parts for g in p.group_ids if g in groups
    ]
    for a, b in combinations(course_groups, 2):
        if _groups_conflict(a, b):
            add(
                "conflicting_groups",
                f"{path}.parts",
                f"groups {a.id} and {b.id} share students and cannot be in one course",
            )


def _check_part_room(part, pp, add, rooms) -> None:
    if not part.needs_room:
        if part.room_type is not None or part.allowed_room_ids is not None:
            add(
                "room_without_needs_room",
                pp,
                "room fields are set but needs_room is false",
            )
        return
    for rid in part.allowed_room_ids or ():
        if rid not in rooms:
            add("unknown_reference", f"{pp}.allowed_room_ids", f"no room {rid}")
    candidates = [
        r
        for r in rooms.values()
        if (part.room_type is None or r.type == part.room_type)
        and (part.allowed_room_ids is None or r.id in part.allowed_room_ids)
    ]
    if not candidates:
        add("no_matching_room", pp, "no room matches room_type and allowed_room_ids")


def _check_constraint(
    con, path, inp, add, teacher_ids, subject_ids, course_ids
) -> None:
    """Type name, weight and references only.

    Full params validation (required keys, value types) is the Pydantic
    models' job (BE-03). Here we read params defensively, so that a deleted
    teacher, subject, course or class is reported instead of silently
    ignored by the solver.
    """
    if con.type not in CONSTRAINT_TYPES:
        add("unknown_constraint_type", f"{path}.type", f"unknown type {con.type!r}")
    if con.hard and con.weight is not None:
        add("bad_weight", f"{path}.weight", "hard constraints have no weight")
    if not con.hard and (con.weight is None or con.weight < 1):
        add(
            "bad_weight",
            f"{path}.weight",
            "soft constraints need a weight of at least 1",
        )

    p = con.params
    days, periods = set(inp.days), set(inp.periods)

    def ref(key: str, known: set[int], label: str) -> None:
        if key in p and p[key] not in known:
            add("unknown_reference", f"{path}.params.{key}", f"no {label} {p[key]!r}")

    ref("teacher_id", teacher_ids, "teacher")
    ref("subject_id", subject_ids, "subject")
    ref("course_id", course_ids, "course")

    scope = p.get("scope")
    if isinstance(scope, dict):
        if "class_id" in scope and scope["class_id"] not in {c.id for c in inp.classes}:
            add(
                "unknown_reference",
                f"{path}.params.scope",
                f"no class {scope['class_id']!r}",
            )
        if "grade" in scope and scope["grade"] not in {c.grade for c in inp.classes}:
            add(
                "unknown_reference",
                f"{path}.params.scope",
                f"no class in grade {scope['grade']!r}",
            )

    if "room_type" in p and p["room_type"] not in {r.type for r in inp.rooms}:
        add(
            "unknown_reference",
            f"{path}.params.room_type",
            f"no room of type {p['room_type']!r}",
        )

    if isinstance(p.get("days"), list):
        bad = [d for d in p["days"] if d not in days]
        if bad:
            add(
                "out_of_range", f"{path}.params.days", f"days {bad} are not school days"
            )
    if isinstance(p.get("slots"), list):
        for si, s in enumerate(p["slots"]):
            if isinstance(s, dict) and (
                s.get("day") not in days or s.get("period") not in periods
            ):
                add(
                    "out_of_range",
                    f"{path}.params.slots[{si}]",
                    "slot is outside the school week",
                )

    if con.type == "fixed_slot":
        _check_fixed_slot(p, path, inp, add)


def _check_fixed_slot(p, path, inp, add) -> None:
    """fixed_slot pins every block of a Course: params.blocks[i] = {day, start_period}.

    blocks[i] belongs to course.block_sizes[i], so the list must have one entry
    per block and each block must fit in the school day.
    """
    course = next((c for c in inp.courses if c.id == p.get("course_id")), None)
    blocks = p.get("blocks")
    if course is None or not isinstance(blocks, list):
        return  # unknown course already reported; shape is BE-03's job
    if len(blocks) != len(course.block_sizes):
        add(
            "fixed_slot_block_count",
            f"{path}.params.blocks",
            f"course {course.id} has {len(course.block_sizes)} blocks, "
            f"got {len(blocks)} positions",
        )
        return
    last_period = inp.periods[-1] if inp.periods else 0
    placed = []
    for bi, (b, size) in enumerate(zip(blocks, course.block_sizes, strict=True)):
        if not isinstance(b, dict):
            continue
        day, start = b.get("day"), b.get("start_period")
        if day not in inp.days or start not in inp.periods:
            add(
                "out_of_range",
                f"{path}.params.blocks[{bi}]",
                "block is outside the school week",
            )
            continue
        if start + size - 1 > last_period:
            add(
                "out_of_range",
                f"{path}.params.blocks[{bi}]",
                f"a {size}-period block starting at period {start} ends after the day",
            )
        placed.append((day, start, start + size - 1))
    # Blocks of one course on one day may be back to back but must not overlap
    # (no gap rule, decided 14.11.2026, TECH_STACK §15 q10).
    for (d1, s1, e1), (d2, s2, e2) in combinations(placed, 2):
        if d1 == d2 and s1 <= e2 and s2 <= e1:
            add(
                "out_of_range",
                f"{path}.params.blocks",
                "two blocks of one course on one day overlap",
            )
