"""member role, can_spend_credits, user-account access allow-list

Revision ID: c3e82a51d477
Revises: b7d41f2c9a10
Create Date: 2026-07-07 21:10:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c3e82a51d477'
down_revision = 'b7d41f2c9a10'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The role column is a plain VARCHAR on SQLite (no CHECK constraint was created),
    # so renaming the value is a data update.
    op.execute("UPDATE users SET role = 'member' WHERE role = 'viewer'")
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('can_spend_credits', sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.create_table(
        'user_account_access',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('audible_account_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['audible_account_id'], ['audible_accounts.id']),
        sa.PrimaryKeyConstraint('user_id', 'audible_account_id'),
    )


def downgrade() -> None:
    op.drop_table('user_account_access')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('can_spend_credits')
    op.execute("UPDATE users SET role = 'viewer' WHERE role = 'member'")
