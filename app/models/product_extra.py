"""Cadastro complementar do produto — guardado SÓ no Gerente do Ano.

Decisão do dono em 26/11/2026 (PENDENCIAS A2): o Olist continua a fonte da
verdade e nunca recebe escrita; aqui ficam só informações que o Olist não tem
ou que vêm de outro lugar (fornecedor, custo do Onix, estoque mínimo, local na
prateleira, compatibilidade com as motos).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.models.catalog import Product
from app.models.company import CompanyScoped


class ProductExtra(CompanyScoped, TimestampMixin, Base):
    __tablename__ = "product_extras"
    __table_args__ = (UniqueConstraint("company_id", "product_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    # Marca escrita à mão (vale mais que a do Olist e a do anúncio).
    brand: Mapped[str | None] = mapped_column(String(80), nullable=True)
    onix_brand: Mapped[str | None] = mapped_column(String(80), nullable=True)  # marca no Onix (só leitura)
    supplier: Mapped[str | None] = mapped_column(String(120), nullable=True)
    supplier_code: Mapped[str | None] = mapped_column(String(64), nullable=True)  # código do item no fornecedor
    # Custo informado pelo Onix (loja física) ou pela planilha. É o custo usado no lucro (app/services/costs.py);
    # sem ele, vale o do Olist.
    onix_cost_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    onix_cost_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Última leitura do Onix achou este SKU? None = ainda não consultado.
    onix_found: Mapped[bool | None] = mapped_column(nullable=True)
    min_stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # prazo deste fornecedor
    location: Mapped[str | None] = mapped_column(String(60), nullable=True)  # prateleira/corredor
    # Compatibilidade com motos: [{"marca": "Honda", "modelo": "CG 160", "de": 2016, "ate": 2024}, ...]
    compatibility: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    product: Mapped[Product] = relationship()
