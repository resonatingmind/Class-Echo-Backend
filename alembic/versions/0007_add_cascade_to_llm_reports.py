"""add foreign key cascade ondelete to llm_reports

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop existing constraint
    op.drop_constraint("llm_reports_recording_id_fkey", "llm_reports", type_="foreignkey")
    # Re-create with CASCADE
    op.create_foreign_key(
        "llm_reports_recording_id_fkey",
        "llm_reports",
        "recordings",
        ["recording_id"],
        ["id"],
        ondelete="CASCADE"
    )


def downgrade() -> None:
    op.drop_constraint("llm_reports_recording_id_fkey", "llm_reports", type_="foreignkey")
    op.create_foreign_key(
        "llm_reports_recording_id_fkey",
        "llm_reports",
        "recordings",
        ["recording_id"],
        ["id"]
    )
