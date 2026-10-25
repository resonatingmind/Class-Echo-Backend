"""add processing_step to recordings

Revision ID: b7e2f4a91c38
Revises: a9c4d2e7f150
Create Date: 2026-10-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7e2f4a91c38"
down_revision: Union[str, None] = "a9c4d2e7f150"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("recordings", sa.Column("processing_step", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("recordings", "processing_step")
