"""add class_brief_opens and device_tokens

Revision ID: b9f4d6a2e315
Revises: a8e3c5f1d204
Create Date: 2026-10-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b9f4d6a2e315"
down_revision: Union[str, None] = "a8e3c5f1d204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "class_brief_opens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("teacher_id", sa.Integer(), sa.ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "timetable_entry_id", sa.Integer(),
            sa.ForeignKey("timetable_entries.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("on_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("opened_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("teacher_id", "timetable_entry_id", "on_date", name="uq_class_brief_open"),
    )
    op.create_index("ix_class_brief_opens_id", "class_brief_opens", ["id"])
    op.create_index("ix_class_brief_opens_teacher_id", "class_brief_opens", ["teacher_id"])
    op.create_index("ix_class_brief_opens_timetable_entry_id", "class_brief_opens", ["timetable_entry_id"])

    op.create_table(
        "device_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("teacher_id", sa.Integer(), sa.ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.String(length=255), nullable=False, unique=True),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_device_tokens_id", "device_tokens", ["id"])
    op.create_index("ix_device_tokens_teacher_id", "device_tokens", ["teacher_id"])


def downgrade() -> None:
    op.drop_index("ix_device_tokens_teacher_id", table_name="device_tokens")
    op.drop_index("ix_device_tokens_id", table_name="device_tokens")
    op.drop_table("device_tokens")
    op.drop_index("ix_class_brief_opens_timetable_entry_id", table_name="class_brief_opens")
    op.drop_index("ix_class_brief_opens_teacher_id", table_name="class_brief_opens")
    op.drop_index("ix_class_brief_opens_id", table_name="class_brief_opens")
    op.drop_table("class_brief_opens")
