"""Tabelas do banco. Importar este pacote registra todas no metadata (usado pelo Alembic)."""

from app.models.action import Action
from app.models.agent import Agent, AgentRun
from app.models.ai import AiCache, AiUsage
from app.models.alert import Alert
from app.models.audit import AuditLog
from app.models.catalog import Product, StockSnapshot
from app.models.fee import MarketplaceFee
from app.models.integration import IntegrationAccount
from app.models.invoice import Invoice
from app.models.listing import Listing
from app.models.order import Order, OrderItem
from app.models.postsale import Claim, Conversation, ConversationMessage
from app.models.question import Question
from app.models.report import Report
from app.models.settings import StoreSetting
from app.models.shipment import Shipment
from app.models.sync import SyncRun
from app.models.user import User

__all__ = [
    "Action",
    "AiCache",
    "Agent",
    "AgentRun",
    "AiUsage",
    "Alert",
    "AuditLog",
    "Claim",
    "Conversation",
    "ConversationMessage",
    "IntegrationAccount",
    "MarketplaceFee",
    "Invoice",
    "Listing",
    "Order",
    "OrderItem",
    "Product",
    "Question",
    "Report",
    "StockSnapshot",
    "Shipment",
    "StoreSetting",
    "SyncRun",
    "User",
]
