"""add rubric_version to llm_reports and the report_feedback table

Revision ID: c1d5e8f2a604
Revises: b7e2f4a91c38
Create Date: 2026-10-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c1d5e8f2a604"
down_revision: Union[str, None] = "b7e2f4a91c38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("llm_reports", sa.Column("rubric_version", sa.String(length=20), nullable=True))
    # Every report so far was scored by the free-form prompt.
    op.execute("UPDATE llm_reports SET rubric_version = '1'")

    op.create_table(
        "report_feedback",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "report_id", sa.Integer(),
            sa.ForeignKey("llm_reports.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "teacher_id", sa.Integer(),
            sa.ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("target_key", sa.String(length=50), nullable=False),
        sa.Column("area", sa.String(length=50), nullable=True),
        sa.Column("verdict", sa.String(length=20), nullable=False, index=True),
        sa.Column("reasons", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("report_id", "target_type", "target_key", name="uq_report_feedback_target"),
    )


def downgrade() -> None:
    op.drop_table("report_feedback")
    op.drop_column("llm_reports", "rubric_version")
