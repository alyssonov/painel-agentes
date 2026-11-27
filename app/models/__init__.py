"""Tabelas do banco. Importar este pacote registra todas no metadata (usado pelo Alembic)."""

from app.models.action import Action
from app.models.agent import Agent, AgentRun
from app.models.ai import AiCache, AiUsage
from app.models.assistant import AssistantConversation, AssistantMessage
from app.models.alert import Alert
from app.models.audit import AuditLog
from app.models.automation import Automation, AutomationDelivery
from app.models.catalog import Product, StockSnapshot
from app.models.company import Company
from app.models.expense import Expense
from app.models.fee import MarketplaceFee
from app.models.integration import IntegrationAccount
from app.models.invoice import Invoice
from app.models.listing import Listing
from app.models.listing_quality import ListingQuality
from app.models.order import Order, OrderItem
from app.models.payment import Payment
from app.models.price import PriceReference
from app.models.pricing import PricingSimulation
from app.models.product_extra import ProductExtra
from app.models.purchase import PurchaseOrder, PurchaseOrderItem, Supplier
from app.models.postsale import Claim, Conversation, ConversationMessage
from app.models.question import Question
from app.models.report import Report
from app.models.review import ClaimReason, ItemRating, ProductInsight, ProductReview
from app.models.settings import StoreSetting
from app.models.shipment import Shipment
from app.models.sync import SyncRun
from app.models.user import User

__all__ = [
    "Automation",
    "AutomationDelivery",
    "Action",
    "AiCache",
    "Agent",
    "AgentRun",
    "AiUsage",
    "AssistantConversation",
    "AssistantMessage",
    "Alert",
    "AuditLog",
    "Claim",
    "Company",
    "Expense",
    "ClaimReason",
    "Conversation",
    "ConversationMessage",
    "IntegrationAccount",
    "ItemRating",
    "MarketplaceFee",
    "Invoice",
    "Listing",
    "ListingQuality",
    "Order",
    "OrderItem",
    "Payment",
    "PriceReference",
    "PricingSimulation",
    "ProductExtra",
    "PurchaseOrder",
    "PurchaseOrderItem",
    "Product",
    "ProductInsight",
    "ProductReview",
    "Question",
    "Report",
    "StockSnapshot",
    "Shipment",
    "StoreSetting",
    "Supplier",
    "SyncRun",
    "User",
]

# Liga o filtro automático por empresa (precisa vir depois de todas as tabelas).
import app.core.tenant  # noqa: E402,F401
