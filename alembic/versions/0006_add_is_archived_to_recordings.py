"""add is_archived to recordings

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-20
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recordings", sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    op.drop_column("recordings", "is_archived")
