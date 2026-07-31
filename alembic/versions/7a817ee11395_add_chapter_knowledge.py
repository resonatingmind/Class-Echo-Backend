"""add_chapter_knowledge

Revision ID: 7a817ee11395
Revises: 6a817ee11394
Create Date: 2026-06-23 05:32:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a817ee11395'
down_revision: Union[str, Sequence[str], None] = '6a817ee11394'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if 'chapter_knowledge' not in existing_tables:
        op.create_table(
            'chapter_knowledge',
            sa.Column('chapter_id', sa.Integer(), nullable=False),
            sa.Column('chapter_summary', sa.Text(), nullable=False),
            sa.Column('learning_objectives', sa.JSON(), nullable=False),
            sa.Column('key_concepts', sa.JSON(), nullable=False),
            sa.Column('must_cover_topics', sa.JSON(), nullable=False),
            sa.Column('common_misconceptions', sa.JSON(), nullable=False),
            sa.Column('generated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
            sa.ForeignKeyConstraint(['chapter_id'], ['chapters.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('chapter_id')
        )
        op.create_index(op.f('ix_chapter_knowledge_chapter_id'), 'chapter_knowledge', ['chapter_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_chapter_knowledge_chapter_id'), table_name='chapter_knowledge')
    op.drop_table('chapter_knowledge')
