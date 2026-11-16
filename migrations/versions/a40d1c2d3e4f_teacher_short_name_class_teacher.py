"""teachers.short_name and school_classes.class_teacher_id (BE-17: aSc import)

Revision ID: a40d1c2d3e4f
Revises: a33d2e3f4b5c
Create Date: 2026-11-17 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a40d1c2d3e4f"
down_revision = "a33d2e3f4b5c"
branch_labels = None
depends_on = None


def upgrade():
    # Both nullable. Batch mode: SQLite (tests) cannot add a constraint to an existing table.
    with op.batch_alter_table("teachers") as batch:
        batch.add_column(sa.Column("short_name", sa.String(40), nullable=True))
    with op.batch_alter_table("school_classes") as batch:
        batch.add_column(sa.Column("class_teacher_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_school_classes_class_teacher_id_teachers",
            "teachers",
            ["class_teacher_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade():
    with op.batch_alter_table("school_classes") as batch:
        batch.drop_constraint(
            "fk_school_classes_class_teacher_id_teachers", type_="foreignkey"
        )
        batch.drop_column("class_teacher_id")
    with op.batch_alter_table("teachers") as batch:
        batch.drop_column("short_name")
