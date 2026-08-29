"""add_email_logs

Revision ID: c4e1a9f7b302
Revises: 212ecb215a67
Create Date: 2026-08-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c4e1a9f7b302"
down_revision: Union[str, Sequence[str], None] = "212ecb215a67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EMAIL_STATUS_VALUES = ("queued", "sent", "failed", "skipped")
EMAIL_STATUS = postgresql.ENUM(*EMAIL_STATUS_VALUES, name="emailstatus")


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "email_logs" in inspector.get_table_names():
        return

    # create_type=False on the column below: the enum is created once here, and
    # letting the column try again raises DuplicateObject on Postgres.
    EMAIL_STATUS.create(bind, checkfirst=True)

    op.create_table(
        "email_logs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("to_email", sa.String(length=320), nullable=False, index=True),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column(
            "purpose",
            sa.String(length=32),
            nullable=False,
            server_default="general",
            index=True,
        ),
        sa.Column(
            "status",
            # create_type=False: the type was created just above. Letting the
            # column create it again raises DuplicateObject on Postgres.
            postgresql.ENUM(
                *EMAIL_STATUS_VALUES, name="emailstatus", create_type=False
            ),
            nullable=False,
            server_default="queued",
            index=True,
        ),
        sa.Column("backend", sa.String(length=16), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("school_id", sa.Integer(), nullable=True, index=True),
        sa.Column("related_type", sa.String(length=16), nullable=True),
        sa.Column("related_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    # The admin screen's default view is "newest first, optionally one status",
    # which is this index and nothing else.
    op.create_index(
        "ix_email_logs_status_created_at", "email_logs", ["status", "created_at"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "email_logs" not in inspector.get_table_names():
        return
    op.drop_index("ix_email_logs_status_created_at", table_name="email_logs")
    op.drop_table("email_logs")
    EMAIL_STATUS.drop(bind, checkfirst=True)
