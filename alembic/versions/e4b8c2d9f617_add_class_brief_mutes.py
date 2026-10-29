"""add the class_brief_mutes table

Revision ID: e4b8c2d9f617
Revises: d2e6f9a3b715
Create Date: 2026-10-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4b8c2d9f617"
down_revision: Union[str, None] = "d2e6f9a3b715"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "class_brief_mutes",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "teacher_id", sa.Integer(),
            sa.ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "subject_id", sa.Integer(),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("teacher_id", "subject_id", name="uq_class_brief_mute"),
    )


def downgrade() -> None:
    op.drop_table("class_brief_mutes")
