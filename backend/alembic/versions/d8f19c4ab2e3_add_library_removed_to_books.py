"""add library_removed and abs_missing flags to books

Revision ID: d8f19c4ab2e3
Revises: c3e82a51d477
Create Date: 2026-10-21 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'd8f19c4ab2e3'
down_revision = 'c3e82a51d477'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('books', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('library_removed', sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column('library_removed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column('abs_missing', sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_index('ix_books_library_removed', ['library_removed'])


def downgrade() -> None:
    with op.batch_alter_table('books', schema=None) as batch_op:
        batch_op.drop_index('ix_books_library_removed')
        batch_op.drop_column('abs_missing')
        batch_op.drop_column('library_removed_at')
        batch_op.drop_column('library_removed')
