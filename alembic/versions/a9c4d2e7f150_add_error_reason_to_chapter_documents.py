"""add error_reason to chapter_documents

Revision ID: a9c4d2e7f150
Revises: e3f8a1c60b47
Create Date: 2026-10-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9c4d2e7f150"
down_revision: Union[str, None] = "e3f8a1c60b47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chapter_documents", sa.Column("error_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chapter_documents", "error_reason")
