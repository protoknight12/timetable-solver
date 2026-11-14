"""Data entry pages for the simple entities (FE-05a): list / create / edit / delete.

Anyone logged in may read the lists; every write route is ``@admin_required``.
Courses (curriculum) are FE-05b.

Assumptions (no school rule is invented here):
- names need not be unique (only period numbers are);
- a whole-class group is created with the class, is named WHOLE_GROUP_NAME and
  can be neither edited nor deleted;
- deleting a class deletes its extra groups, but only if none of them is used.
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
    GroupForm,
    PeriodForm,
    RoomForm,
    SubjectForm,
    TeacherForm,
    room_types,
)
from app.models import (
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
    return [f"в учебната програма ({names}{f' и още {more}' if more > 0 else ''})"]


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


def room_usages(room: Room) -> list[str]:
    found = []
    lessons = db.session.scalar(
        select(func.count()).select_from(Lesson).where(Lesson.room_id == room.id)
    )
    if lessons:
        found.append(f"в генерирано разписание ({lessons} ч.)")
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
        found.append(f"като позволена стая в учебната програма ({names})")
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

    def url(self, action: str, **values) -> str:
        return url_for(f"data.{self.key}_{action}", **values)


def _add_whole_group(school_class: SchoolClass) -> None:
    school_class.groups.append(StudentGroup(name=WHOLE_GROUP_NAME, is_whole=True))


def _extra_groups(school_class: SchoolClass) -> str:
    extras = sorted(
        (g for g in school_class.groups if not g.is_whole), key=lambda g: g.id
    )
    return ", ".join(g.name for g in extras) or "—"


ENTITIES = (
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
            ("Випуск", lambda c: c.grade or "—"),
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
    ),
)


def _refuse(item_name: str, where: list[str]) -> None:
    flash(
        f"„{item_name}“ не може да се изтрие: използва се {'; '.join(where)}.", "danger"
    )


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
            if item is None:
                item = e.model()
                db.session.add(item)
            form.populate_obj(item)
            if creating and e.after_create:
                e.after_create(item)
            if _save():
                verb = "добавен" if creating else "запазен"
                flash(f"{e.the} е {verb}{e.ending}.", "success")
                return redirect(e.url("list"))
        return render_template(
            "data/form.html",
            form=form,
            heading=e.add_label if creating else f"Редактиране на {e.singular}",
            cancel_url=e.url("list"),
            active=e.key,
            types=room_types() if e.with_room_types else None,
            extra=e.extra_template,
            item=item,
        )

    @admin_required
    def delete_view(item_id):
        item = db.get_or_404(e.model, item_id)
        name, where = e.label(item), e.usages(item)
        if request.method == "POST":
            if where:
                _refuse(name, where)
            else:
                db.session.delete(item)
                if _save():
                    flash(f"{e.the} е изтрит{e.ending}.", "success")
            return redirect(e.url("list"))
        return render_template(
            "data/delete.html",
            heading=f"Изтриване на {e.singular}",
            name=name,
            where=where,
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
            _refuse(group.name, where)
        else:
            db.session.delete(group)
            if _save():
                flash("Групата е изтрита.", "success")
        return _back(group.class_id)
    return render_template(
        "data/delete.html",
        heading="Изтриване на група",
        name=group.name,
        where=where,
        cancel_url=url_for("data.classes_edit", item_id=group.class_id),
        active="classes",
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
