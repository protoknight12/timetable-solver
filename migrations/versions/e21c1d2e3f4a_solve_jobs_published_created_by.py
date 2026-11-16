"""solve_jobs.published_at and created_by_id (BE-21)

Revision ID: e21c1d2e3f4a
Revises: d20b1c2d3e4f
Create Date: 2026-11-16 18:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e21c1d2e3f4a"
down_revision = "d20b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    # batch mode: SQLite (tests) cannot add a constraint to an existing table.
    with op.batch_alter_table("solve_jobs") as batch:
        batch.add_column(sa.Column("published_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("created_by_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_solve_jobs_created_by_id_users",
            "users",
            ["created_by_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade():
    with op.batch_alter_table("solve_jobs") as batch:
        batch.drop_constraint("fk_solve_jobs_created_by_id_users", type_="foreignkey")
        batch.drop_column("created_by_id")
        batch.drop_column("published_at")
