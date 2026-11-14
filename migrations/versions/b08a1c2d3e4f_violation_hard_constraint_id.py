"""violations: hard flag and constraint id

Revision ID: b08a1c2d3e4f
Revises: fc4c7f56d204
Create Date: 2026-11-14 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b08a1c2d3e4f"
down_revision = "fc4c7f56d204"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "violations",
        sa.Column("hard", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column("violations", sa.Column("constraint_id", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("violations", "constraint_id")
    op.drop_column("violations", "hard")
