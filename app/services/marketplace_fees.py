"""Taxas dos marketplaces para a calculadora de preços.

- Mercado Livre (Clássico = gold_special, Premium = gold_pro): consulta a API
  oficial "Custos por vender" (/sites/MLB/listing_prices) para o preço exato,
  com a logística da loja e, quando há anúncio, a categoria do produto.
  Sem conexão: usa a porcentagem de reserva da tabela abaixo (marcada como estimativa).
- Shopee e TikTok Shop: tabela editável no painel (Precificação → Taxas). Os
  valores padrão são os de 2026 mais citados por fontes públicas consultadas em
  26/11/2026 (ver SOURCES) — as fontes divergem em alguns valores; a pessoa
  confere na Central do Vendedor e corrige.
"""

import copy
import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.integrations.errors import IntegrationError, NotConnected
from app.models import Shipment
from app.services.pricing_calc import FeeFn, band_fee, flat_fee
from app.services.store_settings import get_setting, set_setting

logger = logging.getLogger("precificacao")

SETTING_KEY = "marketplace_fees"
CHECKED = "26/11/2026"

DEFAULT_FEES: dict = {
    "shopee": {
        "name": "Shopee",
        "bands": [{"max": 7999, "pct": 20, "fixed": 400}, {"max": 9999, "pct": 14, "fixed": 1600},
                  {"max": 19999, "pct": 14, "fixed": 2000}, {"max": None, "pct": 14, "fixed": 2600}],
        "return_fee": 49,  # taxa de devolução por venda (opcional na calculadora)
        "note": "Tabela CNPJ desde 01/03/2026. Algumas fontes citam R$ 4,50 de taxa fixa até R$ 79,99 "
                "a partir de 01/10/2026 e R$ 3 a mais para CPF com muitos pedidos. Confira na Central do Vendedor.",
    },
    "tiktok": {
        "name": "TikTok Shop",
        "bands": [{"max": 4999, "pct": 10, "fixed": 400}, {"max": None, "pct": 6, "fixed": 600}],
        "return_fee": 0,
        "note": "Tabela desde 15/07/2026. Se participar do Programa de taxas de envio, some 6% (teto R$ 50) "
                "na comissão. Confira na Central do Vendedor.",
    },
    "ml_classico": {"name": "Mercado Livre Clássico", "listing_type": "gold_special", "fallback_pct": 12,
                    "note": "Sem conexão com o Mercado Livre, usa esta % (o ML cita 10% a 14% conforme a categoria)."},
    "ml_premium": {"name": "Mercado Livre Premium", "listing_type": "gold_pro", "fallback_pct": 17,
                   "note": "Sem conexão com o Mercado Livre, usa esta % (o ML cita 15% a 19% conforme a categoria)."},
}
ORDER = ["shopee", "ml_classico", "ml_premium", "tiktok"]

SOURCES = [
    ("Mercado Livre — API oficial Custos por vender", "https://developers.mercadolivre.com.br/pt_br/comissao-por-vender"),
    ("Shopee 2026 (Vendedor Lucrativo)", "https://vendedorlucrativo.com.br/blog/comissao-shopee-2026"),
    ("Shopee 2026 (Irroba)", "https://blog.irroba.com.br/novas-taxas-shopee-2026-guia-de-comissoes-e-frete/"),
    ("TikTok Shop 2026 (Hunter Hub)", "https://hunterhub.com.br/blog/taxas-tiktok-shop/"),
    ("TikTok Shop 2026 (Cowimi)", "https://cowimi.com.br/blog/taxas-do-tiktok-shop"),
]


def load_fees(db: Session) -> dict:
    """Tabela atual: padrão + o que a loja corrigiu no painel."""
    fees = copy.deepcopy(DEFAULT_FEES)
    saved = get_setting(db, SETTING_KEY) or {}
    for key, values in saved.items():
        if key in fees and isinstance(values, dict):
            fees[key].update(values)
    return fees


def save_fees(db: Session, fees: dict) -> None:
    set_setting(db, SETTING_KEY, {k: {kk: vv for kk, vv in v.items() if kk not in ("note",)} for k, v in fees.items()})


def store_logistic_type(db: Session) -> str | None:
    """Logística mais usada pela loja nos envios do ML (coleta, agência...), sem contar o Full."""
    row = db.execute(select(Shipment.logistic_type, func.count()).where(
        Shipment.logistic_type.is_not(None), Shipment.logistic_type != "fulfillment")
        .group_by(Shipment.logistic_type).order_by(func.count().desc()).limit(1)).first()
    return row[0] if row else None


@dataclass
class FeeSource:
    fee: FeeFn
    source: str
    exact: bool
    state: dict | None = None  # {"failed": motivo} quando a API do ML falhou no meio do cálculo

    @property
    def label(self) -> str:
        if self.state and self.state.get("failed"):
            return "estimativa: a tarifa oficial do Mercado Livre não respondeu agora"
        return self.source


def ml_fee(client_factory, listing_type: str, fallback_pct: float, *, category_id: str | None,
           logistic_type: str | None) -> FeeSource:
    """Tarifa do ML pela API oficial (com cache por preço); se não der, a % de reserva."""
    if client_factory is None:
        return FeeSource(flat_fee(fallback_pct), f"estimativa ({fallback_pct:g}%): Mercado Livre não conectado", False)
    cache: dict[int, tuple[float, int]] = {}
    state = {"failed": None}

    def fee(price: int) -> tuple[float, int]:
        if price in cache:
            return cache[price]
        if state["failed"]:
            return fallback_pct, 0
        try:
            with client_factory() as client:
                data = client.get_listing_prices(max(price, 100), listing_type, category_id=category_id,
                                                 logistic_type=logistic_type) or {}
        except (IntegrationError, NotConnected) as exc:
            logger.warning("Tarifa do ML indisponível: %s", exc)
            state["failed"] = str(exc)
            return fallback_pct, 0
        details = data.get("sale_fee_details") or {}
        fixed = round(float(details.get("fixed_fee") or 0) * 100)
        pct = details.get("percentage_fee")
        if pct is None:
            total = round(float(data.get("sale_fee_amount") or 0) * 100)
            pct = (total - fixed) / price * 100 if price else 0
        cache[price] = (float(pct), fixed)
        return cache[price]

    where = "da categoria do anúncio" if category_id else "geral (sem categoria)"
    return FeeSource(fee, f"tarifa oficial do Mercado Livre, {where}", True, state)
