"""Fornecedores e pedidos de compra — só no Gerente do Ano (o Olist não é alterado).

Registrar um pedido de compra serve para a sugestão de compra não pedir de novo
o que já está a caminho. Receber o pedido NÃO mexe no estoque: a entrada da
mercadoria continua sendo feita no Olist (regra: o painel nunca altera estoque).
"""

from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.models.company import CompanyScoped

PO_STATUS = {"aberto": "Pedido feito (a caminho)", "recebido": "Recebido", "cancelado": "Cancelado"}


class Supplier(CompanyScoped, TimestampMixin, Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("company_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    contact: Mapped[str | None] = mapped_column(String(120), nullable=True)  # nome do vendedor
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # prazo de entrega
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PurchaseOrder(CompanyScoped, TimestampMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (UniqueConstraint("company_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[int] = mapped_column(Integer)  # nº sequencial da empresa
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="aberto", index=True)
    ordered_on: Mapped[date] = mapped_column(Date)
    expected_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    supplier: Mapped[Supplier] = relationship()
    items: Mapped[list["PurchaseOrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan",
                                                            order_by="PurchaseOrderItem.id")

    @property
    def total_cents(self) -> int:
        return sum(i.quantity * (i.unit_cost_cents or 0) for i in self.items)

    @property
    def units(self) -> int:
        return sum(i.quantity for i in self.items)


class PurchaseOrderItem(CompanyScoped, TimestampMixin, Base):
    __tablename__ = "purchase_order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True,
                                                   index=True)
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_cost_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    order: Mapped[PurchaseOrder] = relationship(back_populates="items")
