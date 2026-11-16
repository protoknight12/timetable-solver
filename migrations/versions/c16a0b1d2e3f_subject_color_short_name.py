"""subjects: color and short name

Revision ID: c16a0b1d2e3f
Revises: b08a1c2d3e4f
Create Date: 2026-11-16 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c16a0b1d2e3f"
down_revision = "b08a1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("subjects", sa.Column("color", sa.String(length=7), nullable=True))
    op.add_column(
        "subjects", sa.Column("short_name", sa.String(length=20), nullable=True)
    )


def downgrade():
    op.drop_column("subjects", "short_name")
    op.drop_column("subjects", "color")
