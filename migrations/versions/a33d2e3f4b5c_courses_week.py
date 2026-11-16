"""courses.week (BE-18: alternating A/B weeks)

Revision ID: a33d2e3f4b5c
Revises: f22c1d2e3f4b
Create Date: 2026-11-16 22:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a33d2e3f4b5c"
down_revision = "f22c1d2e3f4b"
branch_labels = None
depends_on = None


def upgrade():
    # NULL = every week, "A" / "B" = only that week. Batch mode for SQLite (tests).
    with op.batch_alter_table("courses") as batch:
        batch.add_column(sa.Column("week", sa.String(1), nullable=True))


def downgrade():
    with op.batch_alter_table("courses") as batch:
        batch.drop_column("week")
