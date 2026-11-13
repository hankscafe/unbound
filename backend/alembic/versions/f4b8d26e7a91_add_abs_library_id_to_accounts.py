"""add per-account ABS library id

Revision ID: f4b8d26e7a91
Revises: d8f19c4ab2e3
Create Date: 2026-11-13 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'f4b8d26e7a91'
down_revision = 'd8f19c4ab2e3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('audible_accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('abs_library_id', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('audible_accounts', schema=None) as batch_op:
        batch_op.drop_column('abs_library_id')
