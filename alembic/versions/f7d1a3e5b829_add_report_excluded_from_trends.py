"""add llm_reports.excluded_from_trends

Revision ID: f7d1a3e5b829
Revises: e4b8c2d9f617
Create Date: 2026-10-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7d1a3e5b829"
down_revision: Union[str, None] = "e4b8c2d9f617"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "llm_reports",
        sa.Column("excluded_from_trends", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("llm_reports", "excluded_from_trends")
