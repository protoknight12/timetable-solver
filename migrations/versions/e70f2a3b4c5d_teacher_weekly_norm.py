"""teacher weekly norm (хорариум)

Revision ID: e70f2a3b4c5d
Revises: d60e1f2a3b4c
Create Date: 2026-11-22 20:30:00.000000

Adds the nullable column teachers.weekly_norm. Existing teachers keep NULL (no norm).
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e70f2a3b4c5d"
down_revision = "d60e1f2a3b4c"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("teachers") as batch:
        batch.add_column(sa.Column("weekly_norm", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("teachers") as batch:
        batch.drop_column("weekly_norm")
