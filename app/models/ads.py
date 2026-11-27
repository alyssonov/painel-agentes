"""Mercado Ads (Product Ads do Mercado Livre) — números lidos da API oficial, só leitura.

O painel NÃO cria, pausa nem muda campanhas: só guarda investimento, receita e
vendas para mostrar o retorno e descontar a publicidade do lucro (DRE).
"""

from datetime import date

from sqlalchemy import BigInteger, Date, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.models.company import CompanyScoped


class AdsMetricsMixin:
    """Números do período: investimento, receita gerada pelos anúncios e vendas."""

    cost_cents: Mapped[int] = mapped_column(BigInteger, default=0)  # investimento
    revenue_cents: Mapped[int] = mapped_column(BigInteger, default=0)  # total_amount (direta + indireta)
    direct_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    prints: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[int] = mapped_column(Integer, default=0)  # unidades vendidas por publicidade
    sales: Mapped[int] = mapped_column(Integer, default=0)  # vendas por publicidade (advertising_items_quantity)


class AdCampaign(AdsMetricsMixin, CompanyScoped, TimestampMixin, Base):
    """Campanha, com os números dos últimos `period_days` dias."""

    __tablename__ = "ad_campaigns"
    __table_args__ = (UniqueConstraint("company_id", "campaign_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))  # active | paused
    strategy: Mapped[str | None] = mapped_column(String(30), nullable=True)
    budget_cents: Mapped[int] = mapped_column(BigInteger, default=0)  # orçamento médio diário
    roas_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    period_days: Mapped[int] = mapped_column(Integer, default=30)


class AdDaily(AdsMetricsMixin, CompanyScoped, TimestampMixin, Base):
    """Números de uma campanha num dia (dia no fuso da loja, como o Mercado Livre informa)."""

    __tablename__ = "ad_daily"
    __table_args__ = (UniqueConstraint("company_id", "campaign_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(32), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)


class AdGroup(AdsMetricsMixin, CompanyScoped, TimestampMixin, Base):
    """Anúncio patrocinado (Ad Group: um anúncio, uma família de variações ou um produto de catálogo)."""

    __tablename__ = "ad_groups"
    __table_args__ = (UniqueConstraint("company_id", "ad_group_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ad_group_id: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # item, família ou catálogo
    group_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # ITEM | FAMILY | CATALOG
    title: Mapped[str] = mapped_column(String(255))
    campaign_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    thumbnail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    period_days: Mapped[int] = mapped_column(Integer, default=30)
