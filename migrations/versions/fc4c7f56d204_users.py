"""users

Revision ID: fc4c7f56d204
Revises: 8b8bc276d889
Create Date: 2026-11-14 00:32:42.120616

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "fc4c7f56d204"
down_revision = "8b8bc276d889"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("role IN ('admin', 'viewer')", name="ck_users_role"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )


def downgrade():
    op.drop_table("users")
