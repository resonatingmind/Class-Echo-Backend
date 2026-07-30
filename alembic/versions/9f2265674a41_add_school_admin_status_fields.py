"""add_school_admin_status_fields

Revision ID: 9f2265674a41
Revises: b6f3d9a1c7e2
Create Date: 2026-07-30 00:09:28.297200

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9f2265674a41'
down_revision: Union[str, Sequence[str], None] = 'b6f3d9a1c7e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "school_admins",
        sa.Column("status", sa.String(length=50), nullable=False, server_default="active"),
    )
    op.add_column(
        "school_admins",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "school_admins",
        sa.Column("rejected_reason", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("school_admins", "rejected_reason")
    op.drop_column("school_admins", "email_verified")
    op.drop_column("school_admins", "status")
