"""add shared_clips

Revision ID: a8e3c5f1d204
Revises: f7d1a3e5b829
Create Date: 2026-10-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8e3c5f1d204"
down_revision: Union[str, None] = "f7d1a3e5b829"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shared_clips",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("llm_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("teacher_id", sa.Integer(), sa.ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("start", sa.Float(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("report_id", "start", name="uq_shared_clips_report_start"),
    )
    op.create_index("ix_shared_clips_id", "shared_clips", ["id"])
    op.create_index("ix_shared_clips_report_id", "shared_clips", ["report_id"])
    op.create_index("ix_shared_clips_teacher_id", "shared_clips", ["teacher_id"])


def downgrade() -> None:
    op.drop_index("ix_shared_clips_teacher_id", table_name="shared_clips")
    op.drop_index("ix_shared_clips_report_id", table_name="shared_clips")
    op.drop_index("ix_shared_clips_id", table_name="shared_clips")
    op.drop_table("shared_clips")
