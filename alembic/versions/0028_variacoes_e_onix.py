"""Tipo de variação do Olist (pai/variação) e se o Onix achou o produto

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-27 15:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0028'
down_revision: Union[str, Sequence[str], None] = '0027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('products') as batch_op:
        batch_op.add_column(sa.Column('variation_kind', sa.String(length=1), nullable=True))
    with op.batch_alter_table('product_extras') as batch_op:
        batch_op.add_column(sa.Column('onix_found', sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('product_extras') as batch_op:
        batch_op.drop_column('onix_found')
    with op.batch_alter_table('products') as batch_op:
        batch_op.drop_column('variation_kind')
