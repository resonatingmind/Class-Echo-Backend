"""add_audit_logs

Revision ID: e3f8a1c60b47
Revises: d7b2c4e8a915
Create Date: 2026-08-30 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e3f8a1c60b47"
down_revision: Union[str, Sequence[str], None] = "d7b2c4e8a915"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "audit_logs" in inspector.get_table_names():
        return

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_email", sa.String(length=320), nullable=False, index=True),
        sa.Column("action", sa.String(length=64), nullable=False, index=True),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("target_label", sa.String(length=320), nullable=True),
        sa.Column("school_id", sa.Integer(), nullable=True, index=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
    )
    # The default view is "everything, newest first", optionally narrowed to one
    # action or one school. This covers all three.
    op.create_index(
        "ix_audit_logs_action_created_at", "audit_logs", ["action", "created_at"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "audit_logs" not in inspector.get_table_names():
        return
    op.drop_index("ix_audit_logs_action_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
