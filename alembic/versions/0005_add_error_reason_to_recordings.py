"""add error_reason to recordings

Revision ID: 0005
Revises: f0cf322d27e4
Create Date: 2026-06-20
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "f0cf322d27e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recordings", sa.Column("error_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("recordings", "error_reason")
