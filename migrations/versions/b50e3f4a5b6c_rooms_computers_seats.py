"""fixed room types, computers, seats and student counts (BE-20)

Revision ID: b50e3f4a5b6c
Revises: a40d1c2d3e4f
Create Date: 2026-11-18 10:00:00.000000

Adds rooms.has_computers / rooms.seats, subjects.requires_computers,
school_classes.student_count and student_groups.student_count, and maps the
free-text room types to the fixed codes of app/room_types.py. The mapping is
copied here on purpose: a migration must keep working when the app changes.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b50e3f4a5b6c"
down_revision = "a40d1c2d3e4f"
branch_labels = None
depends_on = None

CODES = {
    "classroom": "класна стая",
    "lab": "лаборатория / stem кабинет",
    "lecture": "лекционна зала",
    "gym": "физкултурен салон",
    "workshop": "работилница",
    "other": "друго",
}
ALIASES = {"стая": "classroom", "regular": "classroom"}
# Obvious words only; everything else becomes "other".
HINTS = (
    ("лаб", "lab"),
    ("stem", "lab"),
    ("спорт", "gym"),
    ("физкулт", "gym"),
    ("gym", "gym"),
    ("работил", "workshop"),
    ("лекци", "lecture"),
)


def map_type(value):
    """Old free-text value -> fixed code. None stays None."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip().casefold()
    if text in CODES:
        return text
    for code, label in CODES.items():
        if text == label:
            return code
    if text in ALIASES:
        return ALIASES[text]
    for hint, code in HINTS:
        if hint in text:
            return code
    return "other"


def upgrade():
    # Plain add_column on purpose (no batch mode): SQLite can add a column in
    # place, whereas a batch rebuild would drop and recreate rooms/subjects,
    # which other tables reference, and fail on a database that has data.
    op.add_column(
        "rooms",
        sa.Column(
            "has_computers", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("rooms", sa.Column("seats", sa.Integer(), nullable=True))
    op.add_column(
        "subjects",
        sa.Column(
            "requires_computers",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("school_classes", sa.Column("student_count", sa.Integer()))
    op.add_column("student_groups", sa.Column("student_count", sa.Integer()))

    bind = op.get_bind()
    for table_name, column in (
        ("rooms", "type"),
        ("subjects", "default_room_type"),
        ("course_parts", "room_type"),
    ):
        table = sa.table(
            table_name, sa.column("id", sa.Integer), sa.column(column, sa.String)
        )
        rows = bind.execute(sa.select(table.c.id, table.c[column])).all()
        for row_id, value in rows:
            if table_name == "rooms" and (value is None or not value.strip()):
                new = "other"
            else:
                new = map_type(value)
            if new != value:
                bind.execute(
                    table.update().where(table.c.id == row_id).values({column: new})
                )

    # Constraints name a room type inside their JSON params.
    constraints = sa.table(
        "constraints",
        sa.column("id", sa.Integer),
        sa.column("type", sa.String),
        sa.column("params", sa.JSON),
    )
    rows = bind.execute(
        sa.select(constraints.c.id, constraints.c.params).where(
            constraints.c.type == "room_type_required"
        )
    ).all()
    for row_id, params in rows:
        if isinstance(params, dict) and isinstance(params.get("room_type"), str):
            new = map_type(params["room_type"])
            if new and new != params["room_type"]:
                bind.execute(
                    constraints.update()
                    .where(constraints.c.id == row_id)
                    .values(params={**params, "room_type": new})
                )


def downgrade():
    # The type codes are not mapped back: a code is a valid free-text type too.
    op.drop_column("student_groups", "student_count")
    op.drop_column("school_classes", "student_count")
    op.drop_column("subjects", "requires_computers")
    op.drop_column("rooms", "seats")
    op.drop_column("rooms", "has_computers")
