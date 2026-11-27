"""Custo digitado à mão (product_extras.cost_manual)

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-27 17:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0029'
down_revision: Union[str, Sequence[str], None] = '0028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('product_extras') as batch_op:
        batch_op.add_column(sa.Column('cost_manual', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('product_extras') as batch_op:
        batch_op.drop_column('cost_manual')
