"""Database models. They mirror schema/contract.py (contract v2), TECH_STACK §5.

Constraint params and violation entity ids hold ids inside JSON, so the
database cannot enforce them. ``SolverInput.problems()`` reports broken
references (e.g. a deleted teacher) instead.
"""

from __future__ import annotations

from datetime import datetime, time

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)

# JSONB in production, plain JSON on SQLite so tests need no Docker.
JsonType = JSON().with_variant(JSONB, "postgresql")

CONSTRAINT_STATUSES = ("proposed", "confirmed", "rejected")
CONSTRAINT_SOURCES = ("manual", "llm")

# UI names: admin = "Администратор", viewer = "Преглед".
ROLES = ("admin", "viewer")

course_part_groups = Table(
    "course_part_groups",
    Base.metadata,
    Column(
        "part_id",
        ForeignKey("course_parts.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "group_id",
        ForeignKey("student_groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

# Teachers are RESTRICT on delete: removing a teacher who still teaches must fail.
course_part_teachers = Table(
    "course_part_teachers",
    Base.metadata,
    Column(
        "part_id",
        ForeignKey("course_parts.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("teacher_id", ForeignKey("teachers.id"), primary_key=True),
)


class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))


class Subject(Base):
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    default_room_type: Mapped[str | None] = mapped_column(String(40))


class SchoolClass(Base):
    __tablename__ = "school_classes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    grade: Mapped[int | None]

    groups: Mapped[list[StudentGroup]] = relationship(
        back_populates="school_class",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class StudentGroup(Base):
    __tablename__ = "student_groups"
    __table_args__ = (
        # Exactly one whole-class group per class (the "at least one" half is
        # the seed's and the future class form's job).
        Index(
            "uq_one_whole_group_per_class",
            "class_id",
            unique=True,
            postgresql_where=text("is_whole"),
            sqlite_where=text("is_whole"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("school_classes.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(80), default="")
    is_whole: Mapped[bool] = mapped_column(Boolean, default=False)
    split: Mapped[str] = mapped_column(String(40), default="", server_default="")

    school_class: Mapped[SchoolClass] = relationship(back_populates="groups")


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    type: Mapped[str] = mapped_column(String(40))
    concurrent_capacity: Mapped[int] = mapped_column(default=1, server_default="1")


class Period(Base):
    __tablename__ = "periods"

    number: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    periods_per_week: Mapped[int]
    block_sizes: Mapped[list[int]] = mapped_column(JsonType)
    label: Mapped[str] = mapped_column(String(120), default="", server_default="")

    parts: Mapped[list[CoursePart]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )


class CoursePart(Base):
    __tablename__ = "course_parts"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"))
    needs_room: Mapped[bool] = mapped_column(Boolean, default=False)
    room_type: Mapped[str | None] = mapped_column(String(40))
    allowed_room_ids: Mapped[list[int] | None] = mapped_column(JsonType)

    course: Mapped[Course] = relationship(back_populates="parts")
    subject: Mapped[Subject] = relationship()
    groups: Mapped[list[StudentGroup]] = relationship(secondary=course_part_groups)
    teachers: Mapped[list[Teacher]] = relationship(secondary=course_part_teachers)


class Constraint(Base):
    __tablename__ = "constraints"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(60))
    params: Mapped[dict] = mapped_column(JsonType)
    hard: Mapped[bool] = mapped_column(Boolean, default=True)
    weight: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    source: Mapped[str] = mapped_column(String(20), default="manual")
    original_text: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SolveJob(Base):
    __tablename__ = "solve_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    solver_status: Mapped[str | None] = mapped_column(String(20))
    objective: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(String(2000))
    time_limit_s: Mapped[int | None]
    base_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("solve_jobs.id", ondelete="SET NULL")
    )


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("solve_jobs.id", ondelete="CASCADE"))
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    part_id: Mapped[int] = mapped_column(
        ForeignKey("course_parts.id", ondelete="CASCADE")
    )
    day: Mapped[int]
    start_period: Mapped[int]
    length: Mapped[int]
    room_id: Mapped[int | None] = mapped_column(
        ForeignKey("rooms.id", ondelete="SET NULL")
    )


class Violation(Base):
    __tablename__ = "violations"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("solve_jobs.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(60))
    entity_ids: Mapped[dict | list] = mapped_column(JsonType)
    message_key: Mapped[str] = mapped_column(String(120))


class User(UserMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'viewer')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
