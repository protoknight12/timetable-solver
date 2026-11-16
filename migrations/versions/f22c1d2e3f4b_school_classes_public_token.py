"""school_classes.public_token (FE-16)

Revision ID: f22c1d2e3f4b
Revises: e21c1d2e3f4a
Create Date: 2026-11-16 21:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f22c1d2e3f4b"
down_revision = "e21c1d2e3f4a"
branch_labels = None
depends_on = None


def upgrade():
    # batch mode: SQLite (tests) cannot add a constraint to an existing table.
    with op.batch_alter_table("school_classes") as batch:
        batch.add_column(sa.Column("public_token", sa.String(32), nullable=True))
        batch.create_unique_constraint(
            "uq_school_classes_public_token", ["public_token"]
        )


def downgrade():
    with op.batch_alter_table("school_classes") as batch:
        batch.drop_constraint("uq_school_classes_public_token", type_="unique")
        batch.drop_column("public_token")
