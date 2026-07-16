"""add cascade delete to chapter chunks

Revision ID: a2d06be6db41
Revises: 59d9e6ecc675
Create Date: 2026-07-15 11:17:58.206980

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2d06be6db41'
down_revision: Union[str, Sequence[str], None] = '59d9e6ecc675'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.drop_constraint(
        "chapter_chunks_document_id_fkey",
        "chapter_chunks",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "chapter_chunks_document_id_fkey",
        "chapter_chunks",
        "chapter_documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade():
    op.drop_constraint(
        "chapter_chunks_document_id_fkey",
        "chapter_chunks",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "chapter_chunks_document_id_fkey",
        "chapter_chunks",
        "chapter_documents",
        ["document_id"],
        ["id"],
    )
