"""Permissões marcadas à mão por pessoa (tela Equipe)

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-26 13:30:00

Só acrescenta uma coluna vazia: quem já existe continua com o papel atual
(os usuários criados até hoje são administradores e mantêm tudo).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0017'
down_revision: Union[str, Sequence[str], None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('permissions', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('permissions')
