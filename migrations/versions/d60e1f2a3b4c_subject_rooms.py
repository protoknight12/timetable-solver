"""rooms a subject is taught in (SUBJ-ROOMS)

Revision ID: d60e1f2a3b4c
Revises: b50e3f4a5b6c
Create Date: 2026-11-18 16:00:00.000000

Adds the association table subject_rooms. Deleting a subject or a room removes
only the link rows (ON DELETE CASCADE on both sides).
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d60e1f2a3b4c"
down_revision = "b50e3f4a5b6c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "subject_rooms",
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("room_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("subject_id", "room_id"),
    )


def downgrade():
    op.drop_table("subject_rooms")
