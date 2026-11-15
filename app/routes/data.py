"""Data entry pages for the simple entities (FE-05a): list / create / edit / delete.

Anyone logged in may read the lists; every write route is ``@admin_required``.
Courses (the curriculum, FE-05b) use the same list/create/edit/delete helper; their
parts are added on the course's edit page.

Assumptions (no school rule is invented here):
- names need not be unique (only period numbers are);
- a whole-class group is created with the class, is named WHOLE_GROUP_NAME and
  can be neither edited nor deleted;
- deleting a class deletes its extra groups, but only if none of them is used;
- a course used by a constraint (``params.course_id``) is not deleted; deleting
  one that already has generated lessons deletes those lessons with it;
- a part may not repeat a teacher or conflicting groups of another part of the
  same course (all parts of a course run at the same time).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth import admin_required
from app.forms import (
    ClassForm,
    CourseForm,
    GroupForm,
    PartForm,
    PeriodForm,
    RoomForm,
    SubjectForm,
    TeacherForm,
    group_label,
    room_types,
)
from app.models import (
    Constraint,
    Course,
    CoursePart,
    Lesson,
    Period,
    Room,
    SchoolClass,
    StudentGroup,
    Subject,
    Teacher,
    course_part_groups,
    course_part_teachers,
    db,
)

bp = Blueprint("data", __name__, url_prefix="/data")

WHOLE_GROUP_NAME = "цял клас"
SHOWN_COURSES = 5


# --- where is a row still used? Each function returns phrases, [] when unused. ---


def _course_name(course: Course) -> str:
    return course.label or f"учебен план №{course.id}"


def _in_courses(stmt) -> list[str]:
    courses = db.session.scalars(stmt.order_by(Course.id).distinct()).all()
    if not courses:
        return []
    names = ", ".join(_course_name(c) for c in courses[:SHOWN_COURSES])
    more = len(courses) - SHOWN_COURSES
    return [f"в учебния план ({names}{f' и още {more}' if more > 0 else ''})"]


def _course_parts():
    return select(Course).join(CoursePart, CoursePart.course_id == Course.id)


def teacher_usages(teacher: Teacher) -> list[str]:
    return _in_courses(
        _course_parts()
        .join(course_part_teachers, course_part_teachers.c.part_id == CoursePart.id)
        .where(course_part_teachers.c.teacher_id == teacher.id)
    )


def subject_usages(subject: Subject) -> list[str]:
    return _in_courses(_course_parts().where(CoursePart.subject_id == subject.id))


def _group_usages(group_ids: list[int]) -> list[str]:
    return _in_courses(
        _course_parts()
        .join(course_part_groups, course_part_groups.c.part_id == CoursePart.id)
        .where(course_part_groups.c.group_id.in_(group_ids))
    )


def group_usages(group: StudentGroup) -> list[str]:
    return _group_usages([group.id])


def class_usages(school_class: SchoolClass) -> list[str]:
    return _group_usages([g.id for g in school_class.groups])


def room_note(room: Room) -> str | None:
    # Lesson.room_id is ON DELETE SET NULL, so past lessons do not block deletion.
    lessons = db.session.scalar(
        select(func.count()).select_from(Lesson).where(Lesson.room_id == room.id)
    )
    if lessons:
        return (
            "Стаята присъства в вече генерирани разписания; там тя ще остане без стая."
        )


def room_usages(room: Room) -> list[str]:
    found = []
    # allowed_room_ids is JSON (may hold JSON null), so scan in Python.
    allowed = db.session.execute(
        _course_parts()
        .add_columns(CoursePart.allowed_room_ids)
        .where(CoursePart.allowed_room_ids.is_not(None))
    ).all()
    courses = {c.id: c for c, ids in allowed if ids and room.id in ids}
    if courses:
        names = ", ".join(
            _course_name(c) for c in list(courses.values())[:SHOWN_COURSES]
        )
        found.append(f"като позволена стая в учебния план ({names})")
    return found


# --- generic list / create / edit / delete ---


@dataclass(frozen=True)
class Entity:
    key: str  # URL segment and endpoint prefix
    model: type
    form: type
    plural: str  # page title
    singular: str  # genitive-ready noun: "Редактиране на <singular>"
    add_label: str
    the: str  # "Учителят": subject of "<the> е добавен<ending>."
    ending: str
    columns: tuple[tuple[str, Callable], ...]
    order: tuple
    usages: Callable
    label: Callable = lambda item: item.name
    after_create: Callable | None = None
    extra_template: str | None = None  # shown under the form when editing
    with_room_types: bool = False
    # Full refusal text for rows that may never be deleted (beats usages).
    block: Callable | None = None
    # Informational line on the delete confirmation page.
    note: Callable | None = None
    # (label, value) shown read-only above the form fields when editing.
    fixed: Callable | None = None
    # Creating goes straight to the edit page (a new course still needs parts).
    edit_after_create: bool = False
    created_hint: str = ""

    def url(self, action: str, **values) -> str:
        return url_for(f"data.{self.key}_{action}", **values)


def _next_period(period: Period) -> None:
    # shortcut: two simultaneous creates can pick the same number; the primary
    # key rejects one and _save() reports it. Fine for a handful of admins.
    last = db.session.scalar(select(func.max(Period.number)))
    period.number = (last or 0) + 1


def _period_block(period: Period) -> str | None:
    # The solver input rejects gaps in the numbering ("bad_time_grid").
    if period.number != db.session.scalar(select(func.max(Period.number))):
        return (
            "Може да се изтрие само последният учебен час, "
            "за да няма пропуски в номерацията."
        )


def course_usages(course: Course) -> list[str]:
    # Constraint params are JSON, so the database cannot enforce this reference.
    ids = [
        f"№{c.id}"
        for c in db.session.scalars(select(Constraint).order_by(Constraint.id))
        if isinstance(c.params, dict) and c.params.get("course_id") == course.id
    ]
    return [f"в ограничения ({', '.join(ids)})"] if ids else []


def course_note(course: Course) -> str | None:
    # Lesson.course_id is ON DELETE CASCADE.
    lessons = db.session.scalar(
        select(func.count()).select_from(Lesson).where(Lesson.course_id == course.id)
    )
    if lessons:
        return (
            "Учебният план присъства във вече генерирани разписания; "
            "часовете му ще бъдат изтрити заедно с него."
        )


def _blocks(course: Course) -> str:
    return " + ".join(str(n) for n in course.block_sizes)


def _course_subjects(course: Course) -> str:
    names = sorted({p.subject.name for p in course.parts})
    return ", ".join(names) or "—"


def _course_groups(course: Course) -> str:
    labels = sorted({group_label(g) for p in course.parts for g in p.groups})
    return ", ".join(labels) or "—"


def part_label(part: CoursePart) -> str:
    groups = ", ".join(group_label(g) for g in part.groups)
    return f"{part.subject.name} ({groups})" if groups else part.subject.name


def _add_whole_group(school_class: SchoolClass) -> None:
    school_class.groups.append(StudentGroup(name=WHOLE_GROUP_NAME, is_whole=True))


def _extra_groups(school_class: SchoolClass) -> str:
    extras = sorted(
        (g for g in school_class.groups if not g.is_whole), key=lambda g: g.id
    )
    return ", ".join(g.name for g in extras) or "—"


ENTITIES = (
    Entity(
        "courses",
        Course,
        CourseForm,
        "Учебен план",
        "учебен план",
        "Добави учебен план",
        "Учебният план",
        "",
        (
            ("Име", _course_name),
            ("Часове седмично", lambda c: c.periods_per_week),
            ("Блокове", _blocks),
            ("Предмети", _course_subjects),
            ("Групи", _course_groups),
        ),
        (Course.id,),
        course_usages,
        label=_course_name,
        extra_template="data/_parts.html",
        note=course_note,
        edit_after_create=True,
        created_hint=" Добавете частите му по-долу.",
    ),
    Entity(
        "teachers",
        Teacher,
        TeacherForm,
        "Учители",
        "учител",
        "Добави учител",
        "Учителят",
        "",
        (("Име", lambda t: t.name),),
        (Teacher.name, Teacher.id),
        teacher_usages,
    ),
    Entity(
        "subjects",
        Subject,
        SubjectForm,
        "Предмети",
        "предмет",
        "Добави предмет",
        "Предметът",
        "",
        (
            ("Име", lambda s: s.name),
            ("Тип стая по подразбиране", lambda s: s.default_room_type or "—"),
        ),
        (Subject.name, Subject.id),
        subject_usages,
        with_room_types=True,
    ),
    Entity(
        "rooms",
        Room,
        RoomForm,
        "Стаи",
        "стая",
        "Добави стая",
        "Стаята",
        "а",
        (
            ("Име", lambda r: r.name),
            ("Тип", lambda r: r.type),
            ("Едновременни часове", lambda r: r.concurrent_capacity),
        ),
        (Room.name, Room.id),
        room_usages,
        with_room_types=True,
        note=room_note,
    ),
    Entity(
        "classes",
        SchoolClass,
        ClassForm,
        "Класове",
        "клас",
        "Добави клас",
        "Класът",
        "",
        (
            ("Име", lambda c: c.name),
            ("Година на обучение", lambda c: c.grade or "—"),
            ("Групи", _extra_groups),
        ),
        (SchoolClass.grade, SchoolClass.name, SchoolClass.id),
        class_usages,
        after_create=_add_whole_group,
        extra_template="data/_groups.html",
    ),
    Entity(
        "periods",
        Period,
        PeriodForm,
        "Учебни часове",
        "учебен час",
        "Добави учебен час",
        "Часът",
        "",
        (
            ("Номер", lambda p: p.number),
            ("Начало", lambda p: p.start_time.strftime("%H:%M")),
            ("Край", lambda p: p.end_time.strftime("%H:%M")),
        ),
        (Period.number,),
        lambda period: [],
        label=lambda p: f"Час {p.number}",
        after_create=_next_period,
        block=_period_block,
        fixed=lambda p: ("Номер", p.number),
    ),
)


def _refusal(item_name: str, where: list[str]) -> str:
    return f"„{item_name}“ не може да се изтрие: използва се {'; '.join(where)}."


def _save() -> bool:
    try:
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        flash(
            "Записът не беше запазен: данните влизат в конфликт с други записи.",
            "danger",
        )
        return False


def register(e: Entity) -> None:
    @login_required
    def list_view():
        items = db.session.scalars(select(e.model).order_by(*e.order)).all()
        return render_template("data/list.html", e=e, items=items, active=e.key)

    @admin_required
    def edit_view(item_id=None):
        creating = item_id is None
        item = None if creating else db.get_or_404(e.model, item_id)
        form = e.form(obj=item)
        if form.validate_on_submit():
            if creating:
                item = e.model()
            form.populate_obj(item)
            if creating:
                if e.after_create:
                    e.after_create(item)
                db.session.add(
                    item
                )  # after the hook: its queries must not flush a half-built row
            if _save():
                verb = "добавен" if creating else "запазен"
                hint = e.created_hint if creating else ""
                flash(f"{e.the} е {verb}{e.ending}.{hint}", "success")
                if creating and e.edit_after_create:
                    return redirect(e.url("edit", item_id=item.id))
                return redirect(e.url("list"))
        return render_template(
            "data/form.html",
            form=form,
            heading=e.add_label if creating else f"Редактиране на {e.singular}",
            cancel_url=e.url("list"),
            active=e.key,
            types=room_types() if e.with_room_types else None,
            extra=e.extra_template,
            item=None if creating else item,
            fixed=e.fixed(item) if e.fixed and not creating else None,
        )

    @admin_required
    def delete_view(item_id):
        item = db.get_or_404(e.model, item_id)
        name, where = e.label(item), e.usages(item)
        refusal = (e.block and e.block(item)) or (where and _refusal(name, where))
        if request.method == "POST":
            if refusal:
                flash(refusal, "danger")
            else:
                db.session.delete(item)
                if _save():
                    flash(f"{e.the} е изтрит{e.ending}.", "success")
            return redirect(e.url("list"))
        return render_template(
            "data/delete.html",
            heading=f"Изтриване на {e.singular}",
            name=name,
            refusal=refusal,
            note=e.note(item) if e.note else None,
            cancel_url=e.url("list"),
            active=e.key,
        )

    k = e.key
    both = ["GET", "POST"]
    bp.add_url_rule(f"/{k}/", f"{k}_list", list_view)
    bp.add_url_rule(f"/{k}/new", f"{k}_new", edit_view, methods=both)
    bp.add_url_rule(f"/{k}/<int:item_id>/edit", f"{k}_edit", edit_view, methods=both)
    bp.add_url_rule(
        f"/{k}/<int:item_id>/delete", f"{k}_delete", delete_view, methods=both
    )


for _entity in ENTITIES:
    register(_entity)


# --- groups of a class (the class edit page lists them) ---


def _editable_group(group_id: int):
    """The group, or None after flashing why the whole-class group is off limits."""
    group = db.get_or_404(StudentGroup, group_id)
    if group.is_whole:
        flash("Групата „цял клас“ е част от класа и не може да се променя.", "danger")
        return None
    return group


def _back(class_id: int):
    return redirect(url_for("data.classes_edit", item_id=class_id))


@bp.route("/classes/<int:class_id>/groups/new", methods=["GET", "POST"])
@admin_required
def groups_new(class_id):
    school_class = db.get_or_404(SchoolClass, class_id)
    form = GroupForm()
    if form.validate_on_submit():
        group = StudentGroup(school_class=school_class, is_whole=False)
        form.populate_obj(group)
        db.session.add(group)
        if _save():
            flash("Групата е добавена.", "success")
            return _back(class_id)
    return _group_form(form, "Добави група", school_class)


@bp.route("/groups/<int:group_id>/edit", methods=["GET", "POST"])
@admin_required
def groups_edit(group_id):
    group = _editable_group(group_id)
    if group is None:
        return _back(db.get_or_404(StudentGroup, group_id).class_id)
    form = GroupForm(obj=group)
    if form.validate_on_submit():
        form.populate_obj(group)
        if _save():
            flash("Групата е запазена.", "success")
            return _back(group.class_id)
    return _group_form(form, "Редактиране на група", group.school_class)


@bp.route("/groups/<int:group_id>/delete", methods=["GET", "POST"])
@admin_required
def groups_delete(group_id):
    group = _editable_group(group_id)
    if group is None:
        return _back(db.get_or_404(StudentGroup, group_id).class_id)
    where = group_usages(group)
    if request.method == "POST":
        if where:
            flash(_refusal(group.name, where), "danger")
        else:
            db.session.delete(group)
            if _save():
                flash("Групата е изтрита.", "success")
        return _back(group.class_id)
    return render_template(
        "data/delete.html",
        heading="Изтриване на група",
        name=group.name,
        refusal=where and _refusal(group.name, where),
        note=None,
        cancel_url=url_for("data.classes_edit", item_id=group.class_id),
        active="classes",
    )


# --- parts of a course (the course edit page lists them) ---


@bp.app_template_filter("group_label")
def _group_label_filter(group: StudentGroup) -> str:
    return group_label(group)


def _back_to_course(course_id: int):
    return redirect(url_for("data.courses_edit", item_id=course_id))


def _part_initial(part: CoursePart | None) -> dict:
    if part is None:
        return {}
    return {
        "subject": part.subject_id,
        "groups": [g.id for g in part.groups],
        "teachers": [t.id for t in part.teachers],
        "needs_room": part.needs_room,
        "room_type": part.room_type or "",
        "allowed_room_ids": part.allowed_room_ids or [],
    }


def _fill_part(part: CoursePart, form: PartForm) -> None:
    def rows(model, ids):
        return list(db.session.scalars(select(model).where(model.id.in_(ids))))

    needs = form.needs_room.data
    part.subject = db.session.get(Subject, form.subject.data)
    part.groups = rows(StudentGroup, form.groups.data)
    part.teachers = rows(Teacher, form.teachers.data)
    part.needs_room = needs
    # The room fields only mean something with needs_room (the solver input
    # rejects them otherwise), so they are cleared without it.
    part.room_type = form.room_type.data if needs else None
    part.allowed_room_ids = (
        sorted(form.allowed_room_ids.data or []) or None if needs else None
    )


def _part_page(form, heading, course):
    return render_template(
        "data/part_form.html",
        form=form,
        heading=heading,
        course_name=_course_name(course),
        cancel_url=url_for("data.courses_edit", item_id=course.id),
        active="courses",
        types=room_types(),
    )


def _part_edit(course: Course, part: CoursePart | None):
    creating = part is None
    form = PartForm(course=course, part=part, data=_part_initial(part))
    if form.validate_on_submit():
        if creating:
            part = CoursePart()
        with db.session.no_autoflush:  # the part is not complete yet
            _fill_part(part, form)
        if creating:
            part.course = course
            db.session.add(part)
        if _save():
            flash("Частта е добавена." if creating else "Частта е запазена.", "success")
            return _back_to_course(course.id)
    return _part_page(
        form, "Добави част" if creating else "Редактиране на част", course
    )


@bp.route("/courses/<int:course_id>/parts/new", methods=["GET", "POST"])
@admin_required
def parts_new(course_id):
    return _part_edit(db.get_or_404(Course, course_id), None)


@bp.route("/parts/<int:part_id>/edit", methods=["GET", "POST"])
@admin_required
def parts_edit(part_id):
    part = db.get_or_404(CoursePart, part_id)
    return _part_edit(part.course, part)


@bp.route("/parts/<int:part_id>/delete", methods=["GET", "POST"])
@admin_required
def parts_delete(part_id):
    part = db.get_or_404(CoursePart, part_id)
    course_id = part.course_id
    if request.method == "POST":
        db.session.delete(part)
        if _save():
            flash("Частта е изтрита.", "success")
        return _back_to_course(course_id)
    return render_template(
        "data/delete.html",
        heading="Изтриване на част",
        name=part_label(part),
        refusal=None,
        note=None,
        cancel_url=url_for("data.courses_edit", item_id=course_id),
        active="courses",
    )


def _group_form(form, heading, school_class):
    return render_template(
        "data/form.html",
        form=form,
        heading=heading,
        cancel_url=url_for("data.classes_edit", item_id=school_class.id),
        active="classes",
        types=None,
        extra=None,
        item=None,
    )
