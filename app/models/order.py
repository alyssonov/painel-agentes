"""Pedidos e itens — espelho dos dados do Olist e dos marketplaces."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.models.catalog import Product
from app.models.enums import KanbanStatus


class Order(TimestampMixin, Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), index=True)
    external_id: Mapped[str] = mapped_column(String(64), index=True)  # número no marketplace
    # Identificador do pedido no Olist (único): é por ele que a sincronização atualiza.
    olist_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    olist_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Nota fiscal vinculada no Olist (campo idNotaFiscal do pedido). Só leitura.
    invoice_olist_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Só o nome abreviado do cliente (ex.: "Maria S.") — minimizamos dados pessoais.
    customer_short_name: Mapped[str] = mapped_column(String(80))
    olist_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kanban_status: Mapped[str] = mapped_column(String(20), default=KanbanStatus.TO_PICK, index=True)
    problem_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Valores em centavos.
    items_total_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    marketplace_fee_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    shipping_cost_cents: Mapped[int] = mapped_column(BigInteger, default=0)  # frete pago pela loja
    # Valor que a loja RECEBE pela venda (líquido, já sem taxa, frete e tarifas), igual ao
    # "detalhe da venda" do Mercado Livre — lido no Mercado Pago. None = ainda não conferido.
    net_received_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Se a taxa do marketplace já é conhecida. O Olist não informa a taxa: ela chega
    # pelas APIs do Mercado Livre e da Shopee (Fase 3). Até lá a margem não a inclui.
    fees_known: Mapped[bool] = mapped_column(default=False)
    ordered_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    is_demo: Mapped[bool] = mapped_column(default=False)

    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderItem(TimestampMixin, Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    sku: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_cents: Mapped[int] = mapped_column(BigInteger)

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product | None] = relationship()
