"""users.teacher_id: link a login to a teacher (BE-20)

Revision ID: d20b1c2d3e4f
Revises: c16a0b1d2e3f
Create Date: 2026-11-16 15:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d20b1c2d3e4f"
down_revision = "c16a0b1d2e3f"
branch_labels = None
depends_on = None


def upgrade():
    # batch mode: SQLite (tests) cannot add a constraint to an existing table.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("teacher_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_users_teacher_id_teachers",
            "teachers",
            ["teacher_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint("uq_users_teacher_id", ["teacher_id"])


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_teacher_id", type_="unique")
        batch.drop_constraint("fk_users_teacher_id_teachers", type_="foreignkey")
        batch.drop_column("teacher_id")
