"""add teacher_milestones and schools.streak_min_recordings

Revision ID: c3a7e9b1f426
Revises: b9f4d6a2e315
Create Date: 2026-10-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c3a7e9b1f426"
down_revision: Union[str, None] = "b9f4d6a2e315"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "schools",
        sa.Column("streak_min_recordings", sa.Integer(), nullable=False, server_default="2"),
    )
    op.create_table(
        "teacher_milestones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("teacher_id", sa.Integer(), sa.ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("achieved_at", sa.DateTime(), nullable=False),
        sa.Column("meta", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("shared", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("teacher_id", "key", name="uq_teacher_milestone"),
    )
    op.create_index("ix_teacher_milestones_id", "teacher_milestones", ["id"])
    op.create_index("ix_teacher_milestones_teacher_id", "teacher_milestones", ["teacher_id"])


def downgrade() -> None:
    op.drop_index("ix_teacher_milestones_teacher_id", table_name="teacher_milestones")
    op.drop_index("ix_teacher_milestones_id", table_name="teacher_milestones")
    op.drop_table("teacher_milestones")
    op.drop_column("schools", "streak_min_recordings")
