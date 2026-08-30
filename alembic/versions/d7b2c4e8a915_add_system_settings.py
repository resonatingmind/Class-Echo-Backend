"""add_system_settings

Revision ID: d7b2c4e8a915
Revises: c4e1a9f7b302
Create Date: 2026-08-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d7b2c4e8a915"
down_revision: Union[str, Sequence[str], None] = "c4e1a9f7b302"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "system_settings" in inspector.get_table_names():
        return

    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "system_settings" not in inspector.get_table_names():
        return
    op.drop_table("system_settings")
