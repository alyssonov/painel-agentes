"""Multiempresa: empresa em todos os registros

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-26 12:26:43.129982

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0016'
down_revision: Union[str, Sequence[str], None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Cria a tabela de empresas e liga cada registro à empresa nº 1 (nada é apagado)."""
    op.create_table('companies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('document', sa.String(length=20), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_companies'))
    )
    # A loja de hoje vira a empresa nº 1, dona de todos os dados que já existem
    # (o server_default '1' abaixo preenche company_id em todas as linhas antigas).
    op.execute("INSERT INTO companies (id, name, is_active, created_at, updated_at) "
               "VALUES (1, 'Minha loja', true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
    with op.batch_alter_table('actions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.create_index(batch_op.f('ix_actions_company_id'), ['company_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_actions_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('agent_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.create_index(batch_op.f('ix_agent_runs_company_id'), ['company_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_agent_runs_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('agents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_agents_slug'), type_='unique')
        batch_op.create_index(batch_op.f('ix_agents_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_agents_company_id_slug'), ['company_id', 'slug'])
        batch_op.create_foreign_key(batch_op.f('fk_agents_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('ai_usage', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.create_index(batch_op.f('ix_ai_usage_company_id'), ['company_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_ai_usage_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('alerts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.create_index(batch_op.f('ix_alerts_company_id'), ['company_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_alerts_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.create_index(batch_op.f('ix_audit_log_company_id'), ['company_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_audit_log_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('claim_reasons', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.create_index(batch_op.f('ix_claim_reasons_company_id'), ['company_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_claim_reasons_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('claims', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_claims_channel_external_id'), type_='unique')
        batch_op.create_index(batch_op.f('ix_claims_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_claims_company_id_channel_external_id'), ['company_id', 'channel', 'external_id'])
        batch_op.create_foreign_key(batch_op.f('fk_claims_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('conversation_messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_conversation_messages_conversation_id_external_id'), type_='unique')
        batch_op.create_index(batch_op.f('ix_conversation_messages_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_conversation_messages_company_id_conversation_id_external_id'), ['company_id', 'conversation_id', 'external_id'])
        batch_op.create_foreign_key(batch_op.f('fk_conversation_messages_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_conversations_channel_pack_ref'), type_='unique')
        batch_op.create_index(batch_op.f('ix_conversations_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_conversations_company_id_channel_pack_ref'), ['company_id', 'channel', 'pack_ref'])
        batch_op.create_foreign_key(batch_op.f('fk_conversations_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('integration_accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_integration_accounts_provider_external_id'), type_='unique')
        batch_op.create_index(batch_op.f('ix_integration_accounts_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_integration_accounts_company_id_provider_external_id'), ['company_id', 'provider', 'external_id'])
        batch_op.create_foreign_key(batch_op.f('fk_integration_accounts_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('invoices', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_invoices_olist_id'), type_='unique')
        batch_op.create_index(batch_op.f('ix_invoices_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_invoices_company_id_olist_id'), ['company_id', 'olist_id'])
        batch_op.create_foreign_key(batch_op.f('fk_invoices_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('item_ratings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_item_ratings_channel_item_id'), type_='unique')
        batch_op.create_index(batch_op.f('ix_item_ratings_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_item_ratings_company_id_channel_item_id'), ['company_id', 'channel', 'item_id'])
        batch_op.create_foreign_key(batch_op.f('fk_item_ratings_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('listing_quality', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_listing_quality_channel_item_id'), type_='unique')
        batch_op.create_index(batch_op.f('ix_listing_quality_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_listing_quality_company_id_channel_item_id'), ['company_id', 'channel', 'item_id'])
        batch_op.create_foreign_key(batch_op.f('fk_listing_quality_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('listings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_listings_channel_external_id'), type_='unique')
        batch_op.create_index(batch_op.f('ix_listings_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_listings_company_id_channel_external_id'), ['company_id', 'channel', 'external_id'])
        batch_op.create_foreign_key(batch_op.f('fk_listings_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('marketplace_fees', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_marketplace_fees_channel_order_ref'), type_='unique')
        batch_op.create_index(batch_op.f('ix_marketplace_fees_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_marketplace_fees_company_id_channel_order_ref'), ['company_id', 'channel', 'order_ref'])
        batch_op.create_foreign_key(batch_op.f('fk_marketplace_fees_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('order_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.create_index(batch_op.f('ix_order_items_company_id'), ['company_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_order_items_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_orders_olist_id'), type_='unique')
        batch_op.create_index(batch_op.f('ix_orders_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_orders_company_id_olist_id'), ['company_id', 'olist_id'])
        batch_op.create_foreign_key(batch_op.f('fk_orders_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_payments_channel_payment_id'), type_='unique')
        batch_op.create_index(batch_op.f('ix_payments_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_payments_company_id_channel_payment_id'), ['company_id', 'channel', 'payment_id'])
        batch_op.create_foreign_key(batch_op.f('fk_payments_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('price_references', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_price_references_channel_item_id'), type_='unique')
        batch_op.create_index(batch_op.f('ix_price_references_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_price_references_company_id_channel_item_id'), ['company_id', 'channel', 'item_id'])
        batch_op.create_foreign_key(batch_op.f('fk_price_references_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('pricing_simulations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.create_index(batch_op.f('ix_pricing_simulations_company_id'), ['company_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_pricing_simulations_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('product_insights', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.create_index(batch_op.f('ix_product_insights_company_id'), ['company_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_product_insights_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('product_reviews', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_product_reviews_channel_review_id'), type_='unique')
        batch_op.create_index(batch_op.f('ix_product_reviews_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_product_reviews_company_id_channel_review_id'), ['company_id', 'channel', 'review_id'])
        batch_op.create_foreign_key(batch_op.f('fk_product_reviews_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_products_olist_id'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_products_sku'))
        batch_op.create_index(batch_op.f('ix_products_sku'), ['sku'], unique=False)
        batch_op.create_index(batch_op.f('ix_products_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_products_company_id_olist_id'), ['company_id', 'olist_id'])
        batch_op.create_unique_constraint(batch_op.f('uq_products_company_id_sku'), ['company_id', 'sku'])
        batch_op.create_foreign_key(batch_op.f('fk_products_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('questions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_questions_channel_external_id'), type_='unique')
        batch_op.create_index(batch_op.f('ix_questions_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_questions_company_id_channel_external_id'), ['company_id', 'channel', 'external_id'])
        batch_op.create_foreign_key(batch_op.f('fk_questions_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_reports_kind_period_start'), type_='unique')
        batch_op.create_index(batch_op.f('ix_reports_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_reports_company_id_kind_period_start'), ['company_id', 'kind', 'period_start'])
        batch_op.create_foreign_key(batch_op.f('fk_reports_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('shipments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_shipments_channel_shipment_id'), type_='unique')
        batch_op.create_index(batch_op.f('ix_shipments_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_shipments_company_id_channel_shipment_id'), ['company_id', 'channel', 'shipment_id'])
        batch_op.create_foreign_key(batch_op.f('fk_shipments_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('stock_snapshots', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.create_index(batch_op.f('ix_stock_snapshots_company_id'), ['company_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_stock_snapshots_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('store_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.drop_constraint(batch_op.f('uq_store_settings_key'), type_='unique')
        batch_op.create_index(batch_op.f('ix_store_settings_company_id'), ['company_id'], unique=False)
        batch_op.create_unique_constraint(batch_op.f('uq_store_settings_company_id_key'), ['company_id', 'key'])
        batch_op.create_foreign_key(batch_op.f('fk_store_settings_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('sync_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.create_index(batch_op.f('ix_sync_runs_company_id'), ['company_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_sync_runs_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=False, server_default='1'))
        batch_op.create_index(batch_op.f('ix_users_company_id'), ['company_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_users_company_id_companies'), 'companies', ['company_id'], ['id'], ondelete='CASCADE')

    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_users_company_id_companies'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_users_company_id'))
        batch_op.drop_column('company_id')

    with op.batch_alter_table('sync_runs', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_sync_runs_company_id_companies'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_sync_runs_company_id'))
        batch_op.drop_column('company_id')

    with op.batch_alter_table('store_settings', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_store_settings_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_store_settings_company_id_key'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_store_settings_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_store_settings_key'), ['key'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('stock_snapshots', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_stock_snapshots_company_id_companies'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_stock_snapshots_company_id'))
        batch_op.drop_column('company_id')

    with op.batch_alter_table('shipments', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_shipments_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_shipments_company_id_channel_shipment_id'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_shipments_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_shipments_channel_shipment_id'), ['channel', 'shipment_id'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_reports_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_reports_company_id_kind_period_start'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_reports_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_reports_kind_period_start'), ['kind', 'period_start'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('questions', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_questions_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_questions_company_id_channel_external_id'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_questions_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_questions_channel_external_id'), ['channel', 'external_id'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_products_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_products_company_id_sku'), type_='unique')
        batch_op.drop_constraint(batch_op.f('uq_products_company_id_olist_id'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_products_company_id'))
        batch_op.drop_index(batch_op.f('ix_products_sku'))
        batch_op.create_index(batch_op.f('ix_products_sku'), ['sku'], unique=1)
        batch_op.create_unique_constraint(batch_op.f('uq_products_olist_id'), ['olist_id'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('product_reviews', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_product_reviews_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_product_reviews_company_id_channel_review_id'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_product_reviews_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_product_reviews_channel_review_id'), ['channel', 'review_id'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('product_insights', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_product_insights_company_id_companies'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_product_insights_company_id'))
        batch_op.drop_column('company_id')

    with op.batch_alter_table('pricing_simulations', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_pricing_simulations_company_id_companies'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_pricing_simulations_company_id'))
        batch_op.drop_column('company_id')

    with op.batch_alter_table('price_references', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_price_references_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_price_references_company_id_channel_item_id'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_price_references_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_price_references_channel_item_id'), ['channel', 'item_id'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_payments_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_payments_company_id_channel_payment_id'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_payments_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_payments_channel_payment_id'), ['channel', 'payment_id'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_orders_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_orders_company_id_olist_id'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_orders_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_orders_olist_id'), ['olist_id'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('order_items', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_order_items_company_id_companies'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_order_items_company_id'))
        batch_op.drop_column('company_id')

    with op.batch_alter_table('marketplace_fees', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_marketplace_fees_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_marketplace_fees_company_id_channel_order_ref'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_marketplace_fees_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_marketplace_fees_channel_order_ref'), ['channel', 'order_ref'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('listings', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_listings_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_listings_company_id_channel_external_id'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_listings_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_listings_channel_external_id'), ['channel', 'external_id'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('listing_quality', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_listing_quality_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_listing_quality_company_id_channel_item_id'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_listing_quality_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_listing_quality_channel_item_id'), ['channel', 'item_id'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('item_ratings', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_item_ratings_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_item_ratings_company_id_channel_item_id'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_item_ratings_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_item_ratings_channel_item_id'), ['channel', 'item_id'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('invoices', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_invoices_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_invoices_company_id_olist_id'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_invoices_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_invoices_olist_id'), ['olist_id'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('integration_accounts', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_integration_accounts_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_integration_accounts_company_id_provider_external_id'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_integration_accounts_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_integration_accounts_provider_external_id'), ['provider', 'external_id'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_conversations_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_conversations_company_id_channel_pack_ref'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_conversations_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_conversations_channel_pack_ref'), ['channel', 'pack_ref'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('conversation_messages', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_conversation_messages_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_conversation_messages_company_id_conversation_id_external_id'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_conversation_messages_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_conversation_messages_conversation_id_external_id'), ['conversation_id', 'external_id'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('claims', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_claims_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_claims_company_id_channel_external_id'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_claims_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_claims_channel_external_id'), ['channel', 'external_id'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('claim_reasons', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_claim_reasons_company_id_companies'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_claim_reasons_company_id'))
        batch_op.drop_column('company_id')

    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_audit_log_company_id_companies'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_audit_log_company_id'))
        batch_op.drop_column('company_id')

    with op.batch_alter_table('alerts', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_alerts_company_id_companies'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_alerts_company_id'))
        batch_op.drop_column('company_id')

    with op.batch_alter_table('ai_usage', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_ai_usage_company_id_companies'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_ai_usage_company_id'))
        batch_op.drop_column('company_id')

    with op.batch_alter_table('agents', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_agents_company_id_companies'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_agents_company_id_slug'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_agents_company_id'))
        batch_op.create_unique_constraint(batch_op.f('uq_agents_slug'), ['slug'])
        batch_op.drop_column('company_id')

    with op.batch_alter_table('agent_runs', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_agent_runs_company_id_companies'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_agent_runs_company_id'))
        batch_op.drop_column('company_id')

    with op.batch_alter_table('actions', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_actions_company_id_companies'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_actions_company_id'))
        batch_op.drop_column('company_id')

    op.drop_table('companies')
    # ### end Alembic commands ###
