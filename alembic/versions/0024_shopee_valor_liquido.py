"""Shopee: valor líquido da venda (escrow) junto com a taxa

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-26 21:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0024'
down_revision: Union[str, Sequence[str], None] = '0023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('marketplace_fees', schema=None) as batch_op:
        batch_op.add_column(sa.Column('net_cents', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('marketplace_fees', schema=None) as batch_op:
        batch_op.drop_column('net_cents')
