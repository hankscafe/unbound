"""add abs auto-exclude flags to books

Revision ID: b7d41f2c9a10
Revises: a0c35e89e04f
Create Date: 2026-07-06 22:40:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7d41f2c9a10'
down_revision = 'a0c35e89e04f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('books', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('abs_auto_excluded', sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column('abs_exclude_override', sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table('books', schema=None) as batch_op:
        batch_op.drop_column('abs_exclude_override')
        batch_op.drop_column('abs_auto_excluded')
