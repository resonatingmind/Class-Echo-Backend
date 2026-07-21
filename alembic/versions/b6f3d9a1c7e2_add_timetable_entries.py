"""add timetable_entries table

Revision ID: b6f3d9a1c7e2
Revises: a2d06be6db41
Create Date: 2026-07-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b6f3d9a1c7e2"
down_revision: Union[str, Sequence[str], None] = "a2d06be6db41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "timetable_entries",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("school_id", sa.Integer(), sa.ForeignKey("schools.id"), nullable=False, index=True),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classes.id"), nullable=False, index=True),
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("subjects.id"), nullable=False, index=True),
        sa.Column("teacher_id", sa.Integer(), sa.ForeignKey("teachers.id"), nullable=False, index=True),
        sa.Column("day_of_week", sa.String(length=20), nullable=False),
        sa.Column("period_number", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("room", sa.String(length=100), nullable=True),
        sa.Column(
            "academic_year", sa.String(length=20), nullable=False,
            server_default="2026-27",
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "class_id", "day_of_week", "period_number", "academic_year",
            name="uq_class_period",
        ),
        sa.UniqueConstraint(
            "teacher_id", "day_of_week", "period_number", "academic_year",
            name="uq_teacher_period",
        ),
    )
    op.create_index("ix_timetable_entries_school_id", "timetable_entries", ["school_id"])
    op.create_index("ix_timetable_entries_class_id", "timetable_entries", ["class_id"])
    op.create_index("ix_timetable_entries_subject_id", "timetable_entries", ["subject_id"])
    op.create_index("ix_timetable_entries_teacher_id", "timetable_entries", ["teacher_id"])


def downgrade() -> None:
    op.drop_index("ix_timetable_entries_teacher_id", table_name="timetable_entries")
    op.drop_index("ix_timetable_entries_subject_id", table_name="timetable_entries")
    op.drop_index("ix_timetable_entries_class_id", table_name="timetable_entries")
    op.drop_index("ix_timetable_entries_school_id", table_name="timetable_entries")
    op.drop_table("timetable_entries")
