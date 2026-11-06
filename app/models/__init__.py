"""Tabelas do banco. Importar este pacote registra todas no metadata (usado pelo Alembic)."""

from app.models.agent import Agent, AgentRun
from app.models.ai import AiUsage
from app.models.alert import Alert
from app.models.audit import AuditLog
from app.models.catalog import Product, StockSnapshot
from app.models.fee import MarketplaceFee
from app.models.integration import IntegrationAccount
from app.models.invoice import Invoice
from app.models.order import Order, OrderItem
from app.models.question import Question
from app.models.settings import StoreSetting
from app.models.sync import SyncRun
from app.models.user import User

__all__ = [
    "Agent",
    "AgentRun",
    "AiUsage",
    "Alert",
    "AuditLog",
    "IntegrationAccount",
    "MarketplaceFee",
    "Invoice",
    "Order",
    "OrderItem",
    "Product",
    "Question",
    "StockSnapshot",
    "StoreSetting",
    "SyncRun",
    "User",
]
