"""add transcript_segments, report first_opened_at and the self_ratings table

Revision ID: d2e6f9a3b715
Revises: c1d5e8f2a604
Create Date: 2026-10-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d2e6f9a3b715"
down_revision: Union[str, None] = "c1d5e8f2a604"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("recordings", sa.Column("transcript_segments", postgresql.JSONB(), nullable=True))
    op.add_column("llm_reports", sa.Column("first_opened_at", sa.DateTime(), nullable=True))
    op.add_column("llm_reports", sa.Column("clip_plays", sa.Integer(), nullable=False, server_default="0"))

    op.create_table(
        "self_ratings",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "report_id", sa.Integer(),
            sa.ForeignKey("llm_reports.id", ondelete="CASCADE"), nullable=False, unique=True, index=True,
        ),
        sa.Column(
            "teacher_id", sa.Integer(),
            sa.ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("ratings", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("reflection", sa.Text(), nullable=True),
        sa.Column("skipped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("self_ratings")
    op.drop_column("llm_reports", "clip_plays")
    op.drop_column("llm_reports", "first_opened_at")
    op.drop_column("recordings", "transcript_segments")
